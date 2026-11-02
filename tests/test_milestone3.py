"""Offline Milestone 3 correctness tests; no AWS credentials required."""
import datetime as dt
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from botocore.exceptions import ClientError
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'lambdas/_shared'),str(ROOT/'lambdas/doctor_api'),str(ROOT/'lambdas/slot_materialiser')]
from slot_materialiser_lambda import make_slots,materialise
from doctor_common import doctor_id,ApiError
from doctor_api_lambda import validate_consult

PATTERN={'consultation_type_id':3,'day_of_week':0,'start_time':dt.time(9),
         'end_time':dt.time(11),'duration_minutes':30,'capacity':1}

def test_ist_to_utc_and_stable_ids():
    day=dt.date(2026,11,2)
    slots=make_slots(5,PATTERN,day)
    assert len(slots)==4
    assert slots[0]['startTimeUTC']=='2026-11-02T03:30:00+00:00'
    assert slots[0]['endTimeUTC']=='2026-11-02T04:00:00+00:00'
    assert slots==make_slots(5,PATTERN,day)

def test_other_weekdays_and_partial_slot():
    assert make_slots(5,PATTERN,dt.date(2026,11,3))==[]
    pattern=dict(PATTERN,end_time=dt.time(9,45))
    assert len(make_slots(5,pattern,dt.date(2026,11,2)))==1

def test_doctor_authorizer_identity():
    assert doctor_id({'requestContext':{'authorizer':{'userId':'42','role':'doctor'}}})==42
    with pytest.raises(ApiError):doctor_id({'requestContext':{'authorizer':{'userId':'42','role':'patient'}}})

def test_consultation_rules():
    assert validate_consult({'name':'Health Camp Session','duration_minutes':60,'capacity':3,'fee':'250.00'})[3]==3
    with pytest.raises(ApiError):validate_consult({'name':'Quick Consult','duration_minutes':30,'capacity':1,'fee':100})

class FakeCursor:
    def __init__(self):self.n=0
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,*args):self.n+=1
    def fetchall(self):
        if self.n==1:return [dict(PATTERN,doctor_id=5)]
        if self.n==2:return []
        return [{'user_id':5}]
class FakeConn:
    def cursor(self):return FakeCursor()
    def close(self):pass
class FakeTable:
    def __init__(self):self.rows={}
    def put_item(self,Item,ConditionExpression):
        if Item['slotId'] in self.rows:
            raise ClientError({'Error':{'Code':'ConditionalCheckFailedException','Message':'duplicate'}},'PutItem')
        self.rows[Item['slotId']]=dict(Item)
    def update_item(self,**kwargs):
        raise ClientError({'Error':{'Code':'ConditionalCheckFailedException','Message':'unchanged'}},'UpdateItem')
    def query(self,**kwargs):
        return {'Items':[{'slotId':k,'status':v['status']} for k,v in self.rows.items()]}

def test_repeated_materialisation_does_not_duplicate_or_reset_bookings():
    table=FakeTable()
    run=lambda:materialise(dt.date(2026,11,2),1,table=table,connection_factory=FakeConn)
    first=run()
    assert first['created']==4
    first_slot=next(iter(table.rows.values()))
    first_slot['bookedCount']=1
    second=run()
    assert second['created']==0 and second['alreadyPresent']==4
    assert len(table.rows)==4 and first_slot['bookedCount']==1
