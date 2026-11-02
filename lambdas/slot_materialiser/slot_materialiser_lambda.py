"""Rolling 30-day slot materialisation. Safe to run repeatedly."""
import datetime as dt
import hashlib
import json
import logging
import os
from zoneinfo import ZoneInfo
import boto3
from botocore.exceptions import ClientError
from auth_common import db_connection

IST=ZoneInfo('Asia/Kolkata')
UTC=dt.timezone.utc

def time_of(value):
    if isinstance(value,dt.timedelta):
        seconds=int(value.total_seconds())
        return dt.time(seconds//3600,(seconds%3600)//60)
    if isinstance(value,dt.time):return value
    return dt.time.fromisoformat(str(value))

def make_slots(doctor, pattern, day):
    if day.weekday()!=int(pattern['day_of_week']):return []
    start=time_of(pattern['start_time']);end=time_of(pattern['end_time'])
    cursor=dt.datetime.combine(day,start,tzinfo=IST)
    finish=dt.datetime.combine(day,end,tzinfo=IST)
    duration=dt.timedelta(minutes=int(pattern['duration_minutes']))
    slots=[]
    while cursor+duration<=finish:
        end_at=cursor+duration
        utc_start=cursor.astimezone(UTC).isoformat()
        utc_end=end_at.astimezone(UTC).isoformat()
        identifier=f"{doctor}:{pattern['consultation_type_id']}:{utc_start}"
        slots.append({'slotId':hashlib.sha256(identifier.encode()).hexdigest(),
            'doctorId':str(doctor),'consultationTypeId':str(pattern['consultation_type_id']),
            'startTimeUTC':utc_start,'endTimeUTC':utc_end,'timezone':'Asia/Kolkata',
            'capacity':int(pattern['capacity']),'bookedCount':0,'heldCount':0,
            'version':0,'status':'AVAILABLE'})
        cursor=end_at
    return slots

def materialise(start_day, days=30, table=None, connection_factory=db_connection):
    table=table or boto3.resource('dynamodb').Table(os.environ['SLOTS_TABLE'])
    conn=connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT a.doctor_id,a.consultation_type_id,a.day_of_week,a.start_time,a.end_time,
                  c.duration_minutes,c.capacity FROM doctor_availability a
                  JOIN consultation_types c ON c.consultation_type_id=a.consultation_type_id
                   AND c.doctor_id=a.doctor_id JOIN doctors d ON d.user_id=a.doctor_id
                  WHERE d.timezone=%s""",('Asia/Kolkata',))
            patterns=cur.fetchall()
            cur.execute('SELECT doctor_id,blackout_date FROM doctor_blackouts WHERE blackout_date >= %s AND blackout_date < %s',
                        (start_day,start_day+dt.timedelta(days=days)))
            blackouts={(str(x['doctor_id']),str(x['blackout_date'])) for x in cur.fetchall()}
            cur.execute('SELECT user_id FROM doctors WHERE timezone=%s', ('Asia/Kolkata',))
            doctors={str(x['user_id']) for x in cur.fetchall()}
    finally:conn.close()
    wanted={}
    for offset in range(days):
        day=start_day+dt.timedelta(days=offset)
        for p in patterns:
            doctor=str(p['doctor_id'])
            if (doctor,day.isoformat()) in blackouts:continue
            for slot in make_slots(doctor,p,day):wanted[slot['slotId']]=slot
    created=0;existing=0;disabled=0
    for slot in wanted.values():
        try:
            table.put_item(Item=slot,ConditionExpression='attribute_not_exists(slotId)')
            created+=1
        except ClientError as exc:
            if exc.response['Error']['Code']!='ConditionalCheckFailedException':raise
            # Reactivate previously disabled slots only if unbooked; never reset counters.
            try:
                table.update_item(Key={'slotId':slot['slotId']},
                    UpdateExpression='SET #st=:active',
                    ConditionExpression='#st=:inactive AND bookedCount=:zero AND heldCount=:zero',
                    ExpressionAttributeNames={'#st':'status'},
                    ExpressionAttributeValues={':active':'AVAILABLE',':inactive':'INACTIVE',':zero':0})
            except ClientError as update_exc:
                if update_exc.response['Error']['Code']!='ConditionalCheckFailedException':raise
            existing+=1
    # Suppress obsolete unbooked slots after pattern/blackout edits; never delete booked inventory.
    for doctor in doctors:
        from boto3.dynamodb.conditions import Key
        query={'IndexName':'DoctorStartIndex', 'KeyConditionExpression':
            Key('doctorId').eq(doctor) & Key('startTimeUTC').between(
                dt.datetime.combine(start_day,dt.time.min,tzinfo=IST).astimezone(UTC).isoformat(),
                dt.datetime.combine(start_day+dt.timedelta(days=days),dt.time.min,tzinfo=IST).astimezone(UTC).isoformat()),
            'ProjectionExpression':'slotId,#st', 'ExpressionAttributeNames':{'#st':'status'}}
        while True:
            result=table.query(**query)
            for old in result['Items']:
                if old['slotId'] not in wanted and old.get('status')=='AVAILABLE':
                    try:
                        table.update_item(Key={'slotId':old['slotId']},
                          UpdateExpression='SET #st=:disabled',
                          ConditionExpression='#st=:active AND bookedCount=:zero AND heldCount=:zero',
                          ExpressionAttributeNames={'#st':'status'},
                          ExpressionAttributeValues={':disabled':'INACTIVE',':active':'AVAILABLE',':zero':0})
                        disabled+=1
                    except ClientError as exc:
                        if exc.response['Error']['Code']!='ConditionalCheckFailedException':raise
            if 'LastEvaluatedKey' not in result:break
            query['ExclusiveStartKey']=result['LastEvaluatedKey']
    return {'created':created,'alreadyPresent':existing,'disabled':disabled,'windowStart':start_day.isoformat(),'days':days}

def lambda_handler(event,context):
    start=(event or {}).get('date')
    day=dt.date.fromisoformat(start) if start else dt.datetime.now(IST).date()
    days=int(boto3.client('ssm').get_parameter(Name=os.environ['BOOKING_WINDOW_PARAM'])['Parameter']['Value'])
    if days!=30:raise ValueError('Milestone 3 requires a 30-day window')
    result=materialise(day,days)
    logging.info(json.dumps({'event':'SlotMaterialisationComplete',**result}))
    return result
