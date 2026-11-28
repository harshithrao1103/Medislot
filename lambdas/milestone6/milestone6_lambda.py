"""MediSlot Milestone 6 API and worker.

DynamoDB is the concurrency authority for waitlist registrations and holds. RDS remains
appointment system of record. Notifications use EventBridge -> Lambda -> SES with an
at-most-once dispatch claim (UNKNOWN is never resent automatically).
"""
from __future__ import annotations
import datetime as dt, hashlib, json, logging, os, time, uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
import pymysql
from milestone6_rules import fifo_waiting_entries, offer_is_expired, dispatch_id, reschedule_allowed

log = logging.getLogger(__name__); log.setLevel(logging.INFO)
ddb = boto3.resource('dynamodb')
waitlist = ddb.Table(os.environ['WAITLIST_TABLE'])
slots = ddb.Table(os.environ['SLOTS_TABLE'])
ddb_client = boto3.client('dynamodb')
idempotency = ddb.Table(os.environ.get('IDEMPOTENCY_TABLE','medislot-dev-idempotency'))
dispatches = ddb.Table(os.environ['NOTIFICATION_TABLE'])
ssm = boto3.client('ssm'); events = boto3.client('events'); sf = boto3.client('stepfunctions'); ses = boto3.client('sesv2')

class ApiError(Exception):
    def __init__(self, status, message): self.status, self.message = status, message

def response(status, body, cid):
    return {'statusCode': status, 'headers': {'Content-Type':'application/json','X-Correlation-ID':cid}, 'body':json.dumps(body, default=str)}
def body(event):
    try: return json.loads(event.get('body') or '{}')
    except Exception: raise ApiError(400,'Invalid JSON body')
def identity(event):
    c=(event.get('requestContext') or {}).get('authorizer') or {}
    if str(c.get('role','')).upper()!='PATIENT' or not str(c.get('userId','')).isdigit(): raise ApiError(403,'Patient role required')
    return str(c['userId'])
def param(name, default=None):
    path=os.environ.get('SSM_PREFIX','/medislot/dev/milestone6')+'/'+name
    try: return ssm.get_parameter(Name=path)['Parameter']['Value']
    except ssm.exceptions.ParameterNotFound:
        if default is not None: return str(default)
        raise

def db():
    sec=json.loads(boto3.client('secretsmanager').get_secret_value(SecretId=os.environ['DB_SECRET_ARN'])['SecretString'])
    return pymysql.connect(host=os.environ['DB_HOST'],port=int(os.environ.get('DB_PORT','3306')),user=sec['username'],password=sec['password'],database=os.environ['DB_NAME'],connect_timeout=8,autocommit=False,cursorclass=pymysql.cursors.DictCursor)

def metric(name,value=1):
    log.info(json.dumps({'_aws':{'Timestamp':int(time.time()*1000),'CloudWatchMetrics':[{'Namespace':'medislot','Dimensions':[['Environment']],'Metrics':[{'Name':name,'Unit':'Count'}]}]},name:value,'Environment':os.environ.get('ENVIRONMENT','unknown')}))

def now(): return dt.datetime.now(dt.timezone.utc)
def ttl(seconds): return int(time.time())+seconds

def join_waitlist(event, cid):
    patient=identity(event); slot_id=(event.get('pathParameters') or {}).get('slotId')
    if not slot_id: raise ApiError(400,'slotId is required')
    # Resolve the existing doctorId + slotKey inventory record; a waitlist is only valid for a full slot.
    slot=find_slot(slot_id)
    if slot.get('status')!='AVAILABLE' or int(slot.get('bookedCount',0)) < int(slot.get('capacity',0)):
        raise ApiError(409,'Waitlist registration is only available when the slot is full')
    timestamp=now().isoformat(timespec='microseconds'); entry=uuid.uuid4().hex
    item={'PK':f'SLOT#{slot_id}','SK':f'ENTRY#{timestamp}#{entry}','entityType':'WAITLIST_ENTRY','entryId':entry,'slotId':str(slot_id),'doctorId':str(slot['doctorId']),'slotKey':slot['slotKey'],'patientId':patient,'joinedAt':timestamp,'status':'WAITING','updatedAt':timestamp,'GSI1PK':f'PATIENT#{patient}','GSI1SK':f'JOINED#{timestamp}#{entry}','ttlExpiresAt':ttl(60*60*24*90)}
    try:
        ddb.meta.client.transact_write_items(TransactItems=[
          {'Update':{'TableName':waitlist.name,'Key':{'PK':{'S':f'PATIENT#{patient}'},'SK':{'S':'META'}},'UpdateExpression':'SET activeWaitlistCount=if_not_exists(activeWaitlistCount,:zero)+:one','ConditionExpression':'attribute_not_exists(activeWaitlistCount) OR activeWaitlistCount < :max','ExpressionAttributeValues':{':zero':{'N':'0'},':one':{'N':'1'},':max':{'N':param('max-active-waitlists','5')}}}},
          {'Put':{'TableName':waitlist.name,'Item':{k:serialize(v) for k,v in item.items()},'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}},
          {'Put':{'TableName':waitlist.name,'Item':{'PK':{'S':f'PATIENT#{patient}'},'SK':{'S':f'SLOT#{slot_id}'},'entityType':{'S':'WAITLIST_DEDUP'},'entryId':{'S':entry}},'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}}])
    except ClientError as e:
        if e.response['Error']['Code']=='TransactionCanceledException': raise ApiError(409,'Maximum five active waitlists or duplicate registration')
        raise
    return response(201,{'entryId':entry,'status':'WAITING','slotId':str(slot_id)},cid)

def serialize(v):
    if isinstance(v,str): return {'S':v}
    if isinstance(v,bool): return {'BOOL':v}
    if isinstance(v,int): return {'N':str(v)}
    return {'S':str(v)}

def patient_entries(patient):
    out=[]; key=None
    while True:
        q={'IndexName':'PatientWaitlistIndex','KeyConditionExpression':Key('GSI1PK').eq(f'PATIENT#{patient}'),'ConsistentRead':False}
        if key:q['ExclusiveStartKey']=key
        r=waitlist.query(**q); out.extend(r.get('Items',[])); key=r.get('LastEvaluatedKey')
        if not key:break
    return out

def leave_waitlist(event,cid):
    patient=identity(event); slot_id=(event.get('pathParameters') or {}).get('slotId')
    patient_rows=[x for x in patient_entries(patient) if x.get('slotId')==str(slot_id)]
    matches=[x for x in patient_rows if x.get('status') in ('WAITING','OFFERED')]
    if not matches and any(x.get('status')=='LEFT' for x in patient_rows):
        return response(200,{'status':'LEFT','replayed':True},cid)
    if len(matches)!=1: raise ApiError(404,'Active waitlist entry not found')
    item=matches[0]; offered=item.get('status')=='OFFERED'
    tx=[{'Update':{'TableName':waitlist.name,'Key':{'PK':{'S':item['PK']},'SK':{'S':item['SK']}},'UpdateExpression':'SET #s=:left,updatedAt=:now,offerStatus=:cancel','ConditionExpression':'#s IN (:waiting,:offered)','ExpressionAttributeNames':{'#s':'status'},'ExpressionAttributeValues':{':left':{'S':'LEFT'},':now':{'S':now().isoformat()},':cancel':{'S':'CANCELLED'},':waiting':{'S':'WAITING'},':offered':{'S':'OFFERED'}}}},
         {'Update':{'TableName':waitlist.name,'Key':{'PK':{'S':f'PATIENT#{patient}'},'SK':{'S':'META'}},'UpdateExpression':'SET activeWaitlistCount=activeWaitlistCount - :one','ConditionExpression':'activeWaitlistCount >= :one','ExpressionAttributeValues':{':one':{'N':'1'}}}},
         {'Delete':{'TableName':waitlist.name,'Key':{'PK':{'S':f"PATIENT#{patient}"},'SK':{'S':f"SLOT#{slot_id}"}}}}]
    if offered:
        tx.append({'Update':{'TableName':slots.name,'Key':{'doctorId':{'S':item['doctorId']},'slotKey':{'S':item['slotKey']}},'UpdateExpression':'REMOVE holdOfferId,holdPatientId,holdExpiresAt','ConditionExpression':'holdOfferId=:offer AND holdPatientId=:patient','ExpressionAttributeValues':{':offer':{'S':item['offerId']},':patient':{'S':patient}}}})
    try: ddb.meta.client.transact_write_items(TransactItems=tx)
    except ClientError: raise ApiError(409,'Waitlist entry changed concurrently; refresh and retry')
    if offered: promote({'doctorId':item['doctorId'],'slotId':item['slotId'],'slotKey':item['slotKey']},cid)
    return response(200,{'status':'LEFT'},cid)

def list_waitlists(event,cid):
    patient=identity(event); items=patient_entries(patient)
    if items:
        conn=db()
        try:
            with conn.cursor() as cur:
                for item in items:
                    cur.execute("SELECT u.name,d.hospital_name,d.city FROM users u JOIN doctors d ON d.user_id=u.user_id WHERE u.user_id=%s",(int(item['doctorId']),))
                    doctor=cur.fetchone() or {}
                    item['doctorName']=doctor.get('name') or f"Doctor #{item['doctorId']}"
                    item['hospitalName']=doctor.get('hospital_name') or ''
                    slot_item=slots.get_item(Key={'doctorId':item['doctorId'],'slotKey':item['slotKey']},ConsistentRead=True).get('Item')
                    if slot_item:
                        item['startTimeUTC']=slot_item.get('startTimeUTC')
                        item['endTimeUTC']=slot_item.get('endTimeUTC')
        finally: conn.close()
    return response(200,{'waitlists':items},cid)

def promote(detail,cid='system'):
    hold_seconds=int(param('hold-duration-seconds','900'))
    pk=f"SLOT#{detail['slotId']}"
    kwargs={'KeyConditionExpression':Key('PK').eq(pk),'ScanIndexForward':True}
    while True:
      q=waitlist.query(**kwargs)
      for existing in q.get('Items',[]):
        if existing.get('status')=='OFFERED' and not offer_is_expired(existing['offerExpiresAt'],now()):
            if existing.get('notificationPending'):
                event_id=existing.get('notificationEventId') or f"WaitlistHoldOfferIssued#{existing['offerId']}"
                publish('WaitlistHoldOfferIssued',{'offerId':existing['offerId'],'patientId':existing['patientId'],'slotId':existing['slotId'],'expiresAt':existing['offerExpiresAt'],'eventId':event_id},cid)
                waitlist.update_item(Key={'PK':existing['PK'],'SK':existing['SK']},UpdateExpression='SET notificationPending=:false',ExpressionAttributeValues={':false':False})
            return True
      for entry in fifo_waiting_entries(q.get('Items',[])):
        offer=uuid.uuid4().hex; expiry=now()+dt.timedelta(seconds=hold_seconds); exp=expiry.isoformat(); stamp=now().isoformat()
        try:
            ddb.meta.client.transact_write_items(TransactItems=[
              {'Update':{'TableName':slots.name,'Key':{'doctorId':{'S':str(detail['doctorId'])},'slotKey':{'S':detail['slotKey']}},'UpdateExpression':'SET holdOfferId=:o,holdPatientId=:p,holdExpiresAt=:e','ConditionExpression':'#st=:available AND bookedCount < #cap AND attribute_not_exists(holdOfferId)','ExpressionAttributeNames':{'#st':'status','#cap':'capacity'},'ExpressionAttributeValues':{':o':{'S':offer},':p':{'S':entry['patientId']},':e':{'S':exp},':available':{'S':'AVAILABLE'}}}},
              {'Update':{'TableName':waitlist.name,'Key':{'PK':{'S':entry['PK']},'SK':{'S':entry['SK']}},'UpdateExpression':'SET #s=:offered,offerId=:o,offerStatus=:pending,offeredAt=:now,offerExpiresAt=:exp,updatedAt=:now,notificationPending=:true,notificationEventId=:eventId','ConditionExpression':'#s=:waiting','ExpressionAttributeNames':{'#s':'status'},'ExpressionAttributeValues':{':offered':{'S':'OFFERED'},':waiting':{'S':'WAITING'},':o':{'S':offer},':pending':{'S':'PENDING'},':now':{'S':stamp},':exp':{'S':exp},':true':{'BOOL':True},':eventId':{'S':f'WaitlistHoldOfferIssued#{offer}'}}}}
            ])
            publish('WaitlistHoldOfferIssued',{'offerId':offer,'patientId':entry['patientId'],'slotId':entry['slotId'],'expiresAt':exp,'eventId':f'WaitlistHoldOfferIssued#{offer}'},cid)
            waitlist.update_item(Key={'PK':entry['PK'],'SK':entry['SK']},UpdateExpression='SET notificationPending=:false',ExpressionAttributeValues={':false':False})
            metric('WaitlistPromotions'); return True
        except ClientError as exc:
            if exc.response['Error']['Code']=='TransactionCanceledException': continue
            raise
      if not q.get('LastEvaluatedKey'): break
      kwargs['ExclusiveStartKey']=q['LastEvaluatedKey']
    return False

def publish(kind,detail,cid):
    bus=os.environ['EVENT_BUS_NAME']
    event_id=detail.get('eventId') or (f"{kind}#{detail.get('offerId')}" if detail.get('offerId') else None)
    payload={**detail,'correlationId':cid}
    if event_id: payload['eventId']=event_id
    result=events.put_events(Entries=[{'EventBusName':bus,'Source':'medislot.milestone6','DetailType':kind,'Detail':json.dumps(payload,default=str)}])
    if result.get('FailedEntryCount',0):
        raise RuntimeError(f"EventBridge failed to publish {kind}: {result.get('Entries')}")

def _finalize_accepted_entry(entry,patient,offer_id):
    ddb.meta.client.transact_write_items(TransactItems=[
      {'Update':{'TableName':waitlist.name,'Key':{'PK':{'S':entry['PK']},'SK':{'S':entry['SK']}},'UpdateExpression':'SET #s=:accepted,offerStatus=:accepted,acceptedAt=if_not_exists(acceptedAt,:now),updatedAt=:now','ConditionExpression':'#s=:offered AND offerId=:offer','ExpressionAttributeNames':{'#s':'status'},'ExpressionAttributeValues':{':accepted':{'S':'ACCEPTED'},':offered':{'S':'OFFERED'},':offer':{'S':offer_id},':now':{'S':now().isoformat()}}}},
      {'Update':{'TableName':waitlist.name,'Key':{'PK':{'S':f"PATIENT#{patient}"},'SK':{'S':'META'}},'UpdateExpression':'SET activeWaitlistCount=activeWaitlistCount-:one','ConditionExpression':'activeWaitlistCount >= :one','ExpressionAttributeValues':{':one':{'N':'1'}}}},
      {'Delete':{'TableName':waitlist.name,'Key':{'PK':{'S':f"PATIENT#{patient}"},'SK':{'S':f"SLOT#{entry['slotId']}"}}}}])

def accept_offer(event,cid):
    patient=identity(event); offer_id=(event.get('pathParameters') or {}).get('offerId')
    entries=[x for x in patient_entries(patient) if x.get('offerId')==offer_id]
    if len(entries)!=1: raise ApiError(404,'Offer not found')
    e=entries[0]
    # Recover a prior successful RDS commit if the caller or Lambda retried after a timeout.
    conn=db(); existing=None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT appointment_id FROM appointments WHERE idempotency_key=%s AND patient_id=%s",(f'WAITLIST#{offer_id}',int(patient)))
            existing=cur.fetchone()
        if existing:
            if e.get('status')=='OFFERED':
                guard=waitlist.get_item(Key={'PK':f'PATIENT#{patient}','SK':f"SLOT#{e['slotId']}"},ConsistentRead=True).get('Item')
                if guard: _finalize_accepted_entry(e,patient,offer_id)
            if e.get('status') not in ('OFFERED','ACCEPTED'):
                raise ApiError(409,'This offer is no longer active')
            return response(200,{'appointmentId':existing['appointment_id'],'status':'CONFIRMED','replayed':True},cid)
    finally: conn.close()
    if e.get('status')!='OFFERED': raise ApiError(409,'This offer is no longer active')
    if offer_is_expired(e['offerExpiresAt'],now()): raise ApiError(409,'Offer expired')
    try:
        slots.update_item(Key={'doctorId':e['doctorId'],'slotKey':e['slotKey']},UpdateExpression='SET bookedCount=bookedCount+:one, #v=#v+:one REMOVE holdOfferId,holdPatientId,holdExpiresAt',ConditionExpression='#st=:available AND holdOfferId=:offer AND holdPatientId=:patient AND holdExpiresAt>:now AND bookedCount < #cap',ExpressionAttributeNames={'#v':'version','#st':'status','#cap':'capacity'},ExpressionAttributeValues={':one':1,':offer':offer_id,':patient':patient,':now':now().isoformat(),':available':'AVAILABLE'})
    except ClientError: raise ApiError(409,'Offer is no longer available')
    conn=db(); appointment_id=None; committed=False
    try:
        with conn.cursor() as cur:
            slot_item=slots.get_item(Key={'doctorId':e['doctorId'],'slotKey':e['slotKey']},ConsistentRead=True).get('Item')
            if not slot_item: raise RuntimeError('Slot metadata missing in DynamoDB')
            start_time=dt.datetime.fromisoformat(str(slot_item['startTimeUTC']).replace('Z','+00:00')).replace(tzinfo=None)
            cur.execute("SELECT fee FROM consultation_types WHERE consultation_type_id=%s AND doctor_id=%s",(int(slot_item['consultationTypeId']),int(e['doctorId'])))
            consult=cur.fetchone()
            if not consult: raise RuntimeError('Consultation type metadata missing')
            cur.execute("INSERT INTO appointments(patient_id,doctor_id,slot_id,start_time_utc,status,fee,idempotency_key) VALUES(%s,%s,%s,%s,'CONFIRMED',%s,%s)",(int(patient),int(e['doctorId']),e['slotId'],start_time,consult['fee'],f'WAITLIST#{offer_id}'))
            appointment_id=cur.lastrowid
            payload={'appointmentId':appointment_id,'patientId':int(patient),'doctorId':int(e['doctorId']),'slotId':e['slotId'],'correlationId':cid}
            cur.execute("INSERT INTO event_outbox(event_id,aggregate_type,aggregate_id,event_type,payload,status) VALUES(%s,'APPOINTMENT',%s,'AppointmentBooked',%s,'PENDING')",(uuid.uuid4().hex,str(appointment_id),json.dumps(payload)))
        conn.commit(); committed=True
        metric('AppointmentsBooked')
        if int(updated_slot.get('capacity',0))>0:
            metric('SlotUtilisationPercent',round((int(updated_slot.get('bookedCount',0))/int(updated_slot['capacity']))*100,2),unit='Percent')
        _finalize_accepted_entry(e,patient,offer_id)
        return response(201,{'appointmentId':appointment_id,'status':'CONFIRMED'},cid)
    except Exception:
        try: conn.rollback()
        except Exception: pass
        if not committed:
            # Restore the offer hold if RDS did not commit. The same offer can be retried until expiry.
            slots.update_item(Key={'doctorId':e['doctorId'],'slotKey':e['slotKey']},UpdateExpression='SET bookedCount=bookedCount-:one,#v=#v+:one,holdOfferId=:offer,holdPatientId=:patient,holdExpiresAt=:expiry',ConditionExpression='bookedCount>=:one',ExpressionAttributeNames={'#v':'version'},ExpressionAttributeValues={':one':1,':offer':offer_id,':patient':patient,':expiry':e['offerExpiresAt']})
        raise
    finally: conn.close()

def expire_offers(event,cid):
    # TTL is cleanup only. The minute sweeper performs the business transition and seat release.
    now_iso=now().isoformat()
    expired_filter=Attr('status').eq('OFFERED') & Attr('offerExpiresAt').lt(now_iso)
    notification_retry_filter=Attr('status').eq('EXPIRED') & Attr('notificationPending').eq(True)
    promotion_retry_filter=Attr('status').eq('EXPIRED') & Attr('promotionPending').eq(True)
    kwargs={'FilterExpression':expired_filter | notification_retry_filter | promotion_retry_filter}
    expired=0
    while True:
        page=waitlist.scan(**kwargs)
        for e in page.get('Items',[]):
            try:
                if e.get('status')=='EXPIRED':
                    if e.get('notificationPending'):
                        publish('WaitlistHoldOfferExpired',{'offerId':e['offerId'],'patientId':e['patientId'],'slotId':e['slotId'],'eventId':e.get('notificationEventId') or f"WaitlistHoldOfferExpired#{e['offerId']}"},cid)
                        waitlist.update_item(Key={'PK':e['PK'],'SK':e['SK']},UpdateExpression='SET notificationPending=:false',ExpressionAttributeValues={':false':False})
                    if e.get('promotionPending'):
                        promote({'doctorId':e['doctorId'],'slotId':e['slotId'],'slotKey':e['slotKey']},cid)
                        waitlist.update_item(Key={'PK':e['PK'],'SK':e['SK']},UpdateExpression='SET promotionPending=:false',ExpressionAttributeValues={':false':False})
                    continue
                patient=e['patientId']; event_id=f"WaitlistHoldOfferExpired#{e['offerId']}"
                ddb.meta.client.transact_write_items(TransactItems=[
                  {'Update':{'TableName':slots.name,'Key':{'doctorId':{'S':e['doctorId']},'slotKey':{'S':e['slotKey']}},'UpdateExpression':'REMOVE holdOfferId,holdPatientId,holdExpiresAt','ConditionExpression':'holdOfferId=:o AND holdPatientId=:p','ExpressionAttributeValues':{':o':{'S':e['offerId']},':p':{'S':patient}}}},
                  {'Update':{'TableName':waitlist.name,'Key':{'PK':{'S':e['PK']},'SK':{'S':e['SK']}},'UpdateExpression':'SET #s=:expired,offerStatus=:expired,updatedAt=:now,notificationPending=:true,promotionPending=:true,notificationEventId=:eventId','ConditionExpression':'#s=:offered AND offerId=:o','ExpressionAttributeNames':{'#s':'status'},'ExpressionAttributeValues':{':expired':{'S':'EXPIRED'},':offered':{'S':'OFFERED'},':o':{'S':e['offerId']},':now':{'S':now_iso},':true':{'BOOL':True},':eventId':{'S':event_id}}}},
                  {'Update':{'TableName':waitlist.name,'Key':{'PK':{'S':f'PATIENT#{patient}'},'SK':{'S':'META'}},'UpdateExpression':'SET activeWaitlistCount=activeWaitlistCount-:one','ConditionExpression':'activeWaitlistCount >= :one','ExpressionAttributeValues':{':one':{'N':'1'}}}},
                  {'Delete':{'TableName':waitlist.name,'Key':{'PK':{'S':f"PATIENT#{patient}"},'SK':{'S':f"SLOT#{e['slotId']}"}}}}
                ])
                publish('WaitlistHoldOfferExpired',{'offerId':e['offerId'],'patientId':patient,'slotId':e['slotId'],'eventId':event_id},cid)
                waitlist.update_item(Key={'PK':e['PK'],'SK':e['SK']},UpdateExpression='SET notificationPending=:false',ExpressionAttributeValues={':false':False})
                promote({'doctorId':e['doctorId'],'slotId':e['slotId'],'slotKey':e['slotKey']},cid)
                waitlist.update_item(Key={'PK':e['PK'],'SK':e['SK']},UpdateExpression='SET promotionPending=:false',ExpressionAttributeValues={':false':False})
                expired+=1; metric('OffersExpired')
            except ClientError as exc:
                # A concurrent leave/accept already changed the offer; the transaction prevents double decrement.
                log.info('Offer expiry skipped due to concurrent state change offerId=%s code=%s',e.get('offerId'),exc.response.get('Error',{}).get('Code'))
        if not page.get('LastEvaluatedKey'): break
        kwargs['ExclusiveStartKey']=page['LastEvaluatedKey']
    return {'expired':expired}

def claim_dispatch(dispatch_id,event_type,recipient):
    try:
        dispatches.put_item(Item={'dispatchId':dispatch_id,'status':'PROCESSING','eventType':event_type,'recipientId':str(recipient),'createdAt':now().isoformat()},ConditionExpression='attribute_not_exists(dispatchId)')
        return True
    except ClientError as e:
        if e.response['Error']['Code']=='ConditionalCheckFailedException': return False
        raise

def email_for(cur, user_id):
    cur.execute("SELECT email FROM users WHERE user_id=%s", (user_id,))
    row=cur.fetchone(); return row.get('email') if row else None

def email_content(kind,detail):
    slot=detail.get('slotId') or 'your selected slot'
    start=detail.get('startTimeUTC') or detail.get('start_time_utc') or 'as shown in your MediSlot dashboard'
    if kind=='AppointmentBooked':
        return 'MediSlot appointment confirmed', f"Your appointment has been confirmed.\nSlot: {slot}\nStart (UTC): {start}\nOpen MediSlot to view the full appointment details."
    if kind=='AppointmentRescheduled':
        return 'MediSlot appointment rescheduled', f"Your appointment has been successfully rescheduled.\nNew slot: {slot}\nStart (UTC): {start}\nOpen MediSlot to view the updated appointment."
    if kind=='AppointmentCancelledByPatient':
        return 'MediSlot appointment cancelled', f"An appointment was cancelled by the patient.\nSlot: {slot}\nOpen MediSlot for the appointment and refund details."
    if kind=='AppointmentCancelledByDoctor':
        return 'MediSlot appointment cancelled by doctor', f"Your doctor cancelled the appointment.\nSlot: {slot}\nA full refund is recorded according to MediSlot policy. Open MediSlot for details."
    if kind=='WaitlistHoldOfferIssued':
        return 'MediSlot waitlist offer available', f"A seat is available for your waitlisted slot.\nSlot: {slot}\nOffer expires at: {detail.get('expiresAt','15 minutes after issue')}.\nSign in to MediSlot and accept the offer before it expires."
    if kind=='WaitlistHoldOfferExpired':
        return 'MediSlot waitlist offer expired', f"Your exclusive waitlist offer for slot {slot} has expired. The seat has been released to the next eligible patient."
    if kind=='Reminder24H':
        return 'MediSlot appointment reminder — 24 hours', f"This is a reminder that you have an appointment in approximately 24 hours.\nSlot: {slot}\nStart (UTC): {start}\nPlease open MediSlot to review the details."
    if kind=='Reminder2H':
        return 'MediSlot appointment reminder — 2 hours', f"This is a reminder that you have an appointment in approximately 2 hours.\nSlot: {slot}\nStart (UTC): {start}\nPlease open MediSlot to review the details."
    return f'MediSlot notification: {kind}', json.dumps(detail,default=str)

def send_once(dispatch_id, kind, recipient, detail):
    sender=param('ses-sender-email')
    if '@' not in sender or sender.startswith('CONFIGURE_'):
        raise RuntimeError('Configure a verified SES sender email in SSM before enabling notifications')
    if not claim_dispatch(dispatch_id,kind,recipient): return False
    try:
        subject,message=email_content(kind,detail)
        ses.send_email(FromEmailAddress=sender,Destination={'ToAddresses':[recipient]},Content={'Simple':{'Subject':{'Data':subject},'Body':{'Text':{'Data':message}}}})
        dispatches.update_item(Key={'dispatchId':dispatch_id},UpdateExpression='SET #s=:sent,sentAt=:now',ExpressionAttributeNames={'#s':'status'},ExpressionAttributeValues={':sent':'SENT',':now':now().isoformat()})
        return True
    except Exception as exc:
        # An uncertain SES result is terminal UNKNOWN: do not risk a duplicate send.
        dispatches.update_item(Key={'dispatchId':dispatch_id},UpdateExpression='SET #s=:unknown,lastError=:err',ExpressionAttributeNames={'#s':'status'},ExpressionAttributeValues={':unknown':'UNKNOWN',':err':str(exc)[:500]})
        metric('NotificationSendUnknown')
        return False

def notify(event,cid):
    detail=event.get('detail') or {}; kind=event.get('detail-type') or event.get('DetailType')
    conn=db(); recipients=[]
    try:
        with conn.cursor() as cur:
            appointment_id=detail.get('appointmentId') or detail.get('appointment_id')
            if appointment_id:
                cur.execute("SELECT patient_id,doctor_id,slot_id,start_time_utc FROM appointments WHERE appointment_id=%s FOR UPDATE",(appointment_id,)); ap=cur.fetchone()
                if ap:
                    detail.setdefault('slotId',ap['slot_id'])
                    detail.setdefault('startTimeUTC',ap['start_time_utc'].isoformat() if ap['start_time_utc'] else None)
                    patient_mail=email_for(cur,ap['patient_id']); doctor_mail=email_for(cur,ap['doctor_id'])
                    if kind in ('AppointmentBooked','AppointmentRescheduled'):
                        recipients=[x for x in (patient_mail,doctor_mail) if x]
                    elif kind=='AppointmentCancelled':
                        recipients=[patient_mail] if detail.get('actor')=='DOCTOR' else [x for x in (patient_mail,doctor_mail) if x]
                        kind='AppointmentCancelledByDoctor' if detail.get('actor')=='DOCTOR' else 'AppointmentCancelledByPatient'
            elif detail.get('patientId') or detail.get('patient_id'):
                mail=email_for(cur,detail.get('patientId') or detail.get('patient_id')); recipients=[mail] if mail else []
    finally: conn.close()
    sent=0
    for recipient in recipients:
        identity=detail.get('eventId') or event.get('eventId') or detail.get('appointmentId') or detail.get('appointment_id') or detail.get('offerId') or detail.get('reminderKey')
        did=dispatch_id(identity,kind,recipient)
        sent += bool(send_once(did,kind,recipient,detail))
    metric('NotificationsDispatched',sent); return {'sent':sent,'recipients':len(recipients)}

def reminder_dispatch(event,cid):
    conn=db(); due=[]; current=now().replace(tzinfo=None)
    try:
        with conn.cursor() as cur:
            for label,hours in [('24H',int(param('reminder-24h-hours','24'))),('2H',int(param('reminder-2h-hours','2')))]:
                # Five-minute bucket catches appointments entering the reminder window; status is rechecked here.
                cur.execute("SELECT appointment_id,patient_id,doctor_id,slot_id,start_time_utc FROM appointments WHERE status='CONFIRMED' AND start_time_utc BETWEEN %s AND %s",(current+dt.timedelta(hours=hours)-dt.timedelta(minutes=5),current+dt.timedelta(hours=hours)))
                for ap in cur.fetchall(): due.append((label,ap))
            sent=0
            for label,ap in due:
                recipient=email_for(cur,ap['patient_id'])
                if not recipient: continue
                did=dispatch_id(ap['appointment_id'],f'REMINDER_{label}',ap['patient_id'])
                sent += bool(send_once(did,f"Reminder{label}",recipient,{'appointmentId':ap['appointment_id'],'slotId':ap['slot_id'],'startTimeUTC':ap['start_time_utc'].isoformat()}))
        metric('NotificationsDispatched',sent); return {'examined':len(due),'sent':sent}
    finally: conn.close()

def find_slot(slot_id, doctor_id=None):
    kwargs={'FilterExpression':Attr('slotId').eq(str(slot_id))}
    if doctor_id is not None: kwargs['FilterExpression']=kwargs['FilterExpression'] & Attr('doctorId').eq(str(doctor_id))
    found=[]
    while True:
        r=slots.scan(**kwargs); found.extend(r.get('Items',[]))
        if not r.get('LastEvaluatedKey'): break
        kwargs['ExclusiveStartKey']=r['LastEvaluatedKey']
    if len(found)!=1: raise ApiError(404,'Slot not found or ambiguous')
    return found[0]

def reschedule_start(event,cid):
    patient=identity(event); appointment_id=(event.get('pathParameters') or {}).get('id') or (event.get('pathParameters') or {}).get('appointmentId')
    data=body(event); target_slot_id=str(data.get('slotId') or '')
    if not appointment_id or not target_slot_id: raise ApiError(400,'appointment id and target slotId are required')
    conn=db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT appointment_id,patient_id,doctor_id,slot_id,start_time_utc,status,reschedule_count FROM appointments WHERE appointment_id=%s FOR UPDATE",(appointment_id,)); ap=cur.fetchone()
            if not ap or str(ap['patient_id'])!=patient: raise ApiError(404,'Appointment not found')
            allowed, reason = reschedule_allowed(ap['status'], ap['reschedule_count'], ap['start_time_utc'].replace(tzinfo=dt.timezone.utc), now(), int(param('max-reschedules','2')), int(param('reschedule-cutoff-hours','6')))
            if not allowed: raise ApiError(409, {'NOT_CONFIRMED':'Only confirmed appointments can be rescheduled','LIMIT_REACHED':'Reschedule limit reached','CUTOFF_REACHED':'Rescheduling is closed within the cutoff'}[reason])
        if str(target_slot_id)==str(ap['slot_id']): raise ApiError(409,'Choose a different target slot')
        target=find_slot(target_slot_id,ap['doctor_id']); original=find_slot(ap['slot_id'],ap['doctor_id'])
        target_start=dt.datetime.fromisoformat(str(target['startTimeUTC']).replace('Z','+00:00'))
        if target_start <= now(): raise ApiError(409,'Target slot is in the past')
        saga_id=uuid.uuid4().hex
        execution_input={'sagaId':saga_id,'appointmentId':int(appointment_id),'patientId':int(patient),'doctorId':int(ap['doctor_id']),'originalSlotId':str(ap['slot_id']),'originalSlotKey':original['slotKey'],'targetSlotKey':target['slotKey'],'targetSlotId':target_slot_id,'correlationId':cid}
        with conn.cursor() as cur:
            cur.execute("SELECT saga_id,status,execution_arn FROM reschedule_locks WHERE appointment_id=%s FOR UPDATE",(int(appointment_id),))
            lock=cur.fetchone()
            if lock and lock['status']=='PENDING':
                if lock.get('execution_arn'):
                    conn.commit()
                    return response(202,{'executionArn':lock['execution_arn'],'status':'PENDING','replayed':True},cid)
                raise ApiError(409,'A reschedule is already being started for this appointment')
            cur.execute("INSERT INTO reschedule_locks(appointment_id,saga_id,patient_id,execution_arn,status) VALUES(%s,%s,%s,NULL,'PENDING') ON DUPLICATE KEY UPDATE saga_id=VALUES(saga_id),patient_id=VALUES(patient_id),execution_arn=NULL,status='PENDING'",(int(appointment_id),saga_id,int(patient)))
        # Start the execution while the appointment row/lock row are still in this transaction.
        # The Saga's final RDS commit waits for this transaction; a crash before commit rolls the lock back,
        # causing the Saga's lock-ownership check to fail and compensation to restore any claimed seat.
        started=sf.start_execution(stateMachineArn=os.environ['RESCHEDULE_STATE_MACHINE_ARN'],name=f"rs-{appointment_id}-{saga_id[:16]}",input=json.dumps(execution_input))
        with conn.cursor() as cur:
            cur.execute("UPDATE reschedule_locks SET execution_arn=%s WHERE appointment_id=%s AND saga_id=%s AND status='PENDING'",(started['executionArn'],int(appointment_id),saga_id))
            if cur.rowcount!=1: raise RuntimeError('Could not persist reschedule execution ARN')
        conn.commit()
        return response(202,{'executionArn':started['executionArn'],'status':'PENDING'},cid)
    finally: conn.close()

def reschedule_status(event,cid):
    patient=identity(event); arn=(event.get('queryStringParameters') or {}).get('executionArn')
    if not arn: raise ApiError(400,'executionArn is required')
    result=sf.describe_execution(executionArn=arn); payload=json.loads(result.get('input') or '{}')
    if str(payload.get('patientId'))!=patient: raise ApiError(404,'Execution not found')
    return response(200,{'status':result['status'],'executionArn':arn,'output':result.get('output')},cid)

def saga_marker(saga_id, step):
    return {'PK':f'SAGA#{saga_id}','SK':f'STEP#{step}'}

def saga_task(event,cid):
    op=event['operation']; d=event.get('detail',event); doctor=str(d['doctorId']); appointment=int(d['appointmentId']); saga=str(d.get('sagaId') or d.get('correlationId') or appointment)
    def slot(slot_id):
        try:
            return find_slot(slot_id,doctor)
        except ApiError as exc:
            # Step Functions must observe a failed Task, not a successful Lambda HTTP 404 payload.
            raise RuntimeError(f'Saga slot lookup failed: {exc.message}') from exc
    def marker_exists(step):
        return bool(waitlist.get_item(Key=saga_marker(saga,step),ConsistentRead=True).get('Item'))
    def atomic_slot_step(step, key, update, condition, names, values):
        if marker_exists(step): return
        try:
            ddb_client.transact_write_items(TransactItems=[
              {'Put':{'TableName':waitlist.name,'Item':{'PK':{'S':f'SAGA#{saga}'},'SK':{'S':f'STEP#{step}'},'entityType':{'S':'SAGA_MARKER'},'ttlExpiresAt':{'N':str(ttl(86400*7))}},'ConditionExpression':'attribute_not_exists(PK)'}},
              {'Update':{'TableName':slots.name,'Key':{'doctorId':{'S':str(key['doctorId'])},'slotKey':{'S':str(key['slotKey'])}},'UpdateExpression':update,'ConditionExpression':condition,'ExpressionAttributeNames':names,'ExpressionAttributeValues':values}}
            ])
        except ClientError as exc:
            if exc.response['Error']['Code']=='TransactionCanceledException' and marker_exists(step): return
            raise
        current=slots.get_item(Key={'doctorId':str(key['doctorId']),'slotKey':str(key['slotKey'])},ConsistentRead=True).get('Item')
        if current and int(current.get('capacity',0))>0:
            metric('SlotUtilisationPercent',round((int(current.get('bookedCount',0))/int(current['capacity']))*100,2),unit='Percent')
    if op=='claim_target':
        t=slot(d['targetSlotId']); d['targetSlotKey']=t['slotKey']
        atomic_slot_step('CLAIM_TARGET',{'doctorId':doctor,'slotKey':t['slotKey']},'SET bookedCount=bookedCount+:one,#v=#v+:one','#st=:available AND bookedCount < #cap AND attribute_not_exists(holdOfferId)',{'#v':'version','#st':'status','#cap':'capacity'},{':one':{'N':'1'},':available':{'S':'AVAILABLE'}})
        d['targetClaimed']=True; return d
    if op=='release_original':
        orig=slot(d['originalSlotId']); d['originalSlotKey']=orig['slotKey']
        atomic_slot_step('RELEASE_ORIGINAL',{'doctorId':doctor,'slotKey':orig['slotKey']},'SET bookedCount=bookedCount-:one,#v=#v+:one','bookedCount >= :one',{'#v':'version'},{':one':{'N':'1'}})
        d['originalReleased']=True; return d
    if op=='commit_appointment':
        conn=db()
        try:
            with conn.cursor() as cur:
                payload={'appointmentId':appointment,'patientId':d['patientId'],'doctorId':doctor,'slotId':d['targetSlotId'],'correlationId':d.get('correlationId',cid)}
                try:
                    cur.execute("INSERT INTO event_outbox(event_id,aggregate_type,aggregate_id,event_type,payload,status) VALUES(%s,'APPOINTMENT',%s,'AppointmentRescheduled',%s,'PENDING')",(f"RESCHEDULE#{saga}",str(appointment),json.dumps(payload)))
                    freed_payload={'appointmentId':appointment,'patientId':d['patientId'],'doctorId':doctor,'slotId':d['originalSlotId'],'slotKey':d['originalSlotKey'],'correlationId':d.get('correlationId',cid)}
                    cur.execute("INSERT INTO event_outbox(event_id,aggregate_type,aggregate_id,event_type,payload,status) VALUES(%s,'SLOT',%s,'SlotSeatFreed',%s,'PENDING')",(f"RESCHEDULE_SLOT_FREED#{saga}",str(d['originalSlotId']),json.dumps(freed_payload)))
                except pymysql.err.IntegrityError:
                    # The same saga already committed; deterministic outbox identity makes retries idempotent.
                    conn.rollback(); return {**d,'committed':True,'replayed':True}
                t=find_slot(d['targetSlotId'],doctor)
                target_start=dt.datetime.fromisoformat(str(t['startTimeUTC']).replace('Z','+00:00')).replace(tzinfo=None)
                cur.execute("UPDATE appointments SET slot_id=%s,start_time_utc=%s,reschedule_count=reschedule_count+1 WHERE appointment_id=%s AND patient_id=%s AND status='CONFIRMED' AND reschedule_count < %s",(d['targetSlotId'],target_start,appointment,d['patientId'],int(param('max-reschedules','2'))))
                if cur.rowcount!=1: raise RuntimeError('Appointment update lost its condition')
                cur.execute("UPDATE reschedule_locks SET status='COMPLETED' WHERE appointment_id=%s AND saga_id=%s AND status='PENDING'",(appointment,saga))
                if cur.rowcount!=1: raise RuntimeError('Reschedule lock ownership was lost')
            conn.commit(); metric('ReschedulesSucceeded'); return {**d,'committed':True}
        except Exception: conn.rollback(); raise
        finally: conn.close()
    if op=='compensate':
        # Marker reads make compensation safe even if a prior Lambda completed its write but timed out before returning.
        if marker_exists('RELEASE_ORIGINAL'):
            atomic_slot_step('COMPENSATE_RESTORE_ORIGINAL',{'doctorId':doctor,'slotKey':d['originalSlotKey']},'SET bookedCount=bookedCount+:one,#v=#v+:one','attribute_exists(slotKey)',{'#v':'version'},{':one':{'N':'1'}})
        if marker_exists('CLAIM_TARGET'):
            atomic_slot_step('COMPENSATE_RELEASE_TARGET',{'doctorId':doctor,'slotKey':d['targetSlotKey']},'SET bookedCount=bookedCount-:one,#v=#v+:one','bookedCount >= :one',{'#v':'version'},{':one':{'N':'1'}})
        conn=db()
        try:
            with conn.cursor() as cur: cur.execute("UPDATE reschedule_locks SET status='FAILED' WHERE appointment_id=%s AND saga_id=%s AND status='PENDING'",(appointment,saga))
            conn.commit()
        finally: conn.close()
        log.error('Reschedule compensation completed appointmentId=%s sagaId=%s',appointment,saga); metric('ReschedulesCompensated')
        return {'compensated':True,'appointmentId':appointment}
    raise RuntimeError('Unknown saga operation')

def lambda_handler(event, context):
    cid=(event.get('headers') or {}).get('X-Correlation-ID') or event.get('correlationId') or (event.get('detail') or {}).get('correlationId') or (event.get('requestContext') or {}).get('requestId') or getattr(context,'aws_request_id','system')
    op=event.get('operation') or event.get('detail-type')
    internal_operation = op in {'SlotSeatFreed','expire_offers','reminder_dispatch','notify','claim_target','release_original','commit_appointment','compensate'}
    try:
        if op=='SlotSeatFreed': return {'promoted':promote(event.get('detail',event),cid)}
        if op=='expire_offers': return expire_offers(event,cid)
        if op=='reminder_dispatch': return reminder_dispatch(event,cid)
        if op=='notify': return notify(event,cid)
        if op in ('claim_target','release_original','commit_appointment','compensate'): return saga_task(event,cid)
        if op=='reschedule_status': return reschedule_status(event,cid)
        if op=='join': return join_waitlist(event,cid)
        if op=='leave': return leave_waitlist(event,cid)
        if op=='list': return list_waitlists(event,cid)
        if op=='accept': return accept_offer(event,cid)
        method=event.get('httpMethod'); resource=event.get('resource')
        if resource=='/slots/{slotId}/waitlist' and method=='POST': return join_waitlist(event,cid)
        if resource=='/slots/{slotId}/waitlist' and method=='DELETE': return leave_waitlist(event,cid)
        if resource=='/patients/me/waitlists' and method=='GET': return list_waitlists(event,cid)
        if resource=='/waitlist-offers/{offerId}/accept' and method=='POST': return accept_offer(event,cid)
        if resource=='/appointments/{appointmentId}' and method=='PATCH': return reschedule_start(event,cid)
        if resource=='/reschedule-executions' and method=='GET': return reschedule_status(event,cid)
        return response(404,{'error':'Route not found'},cid)
    except ApiError as e:
        if internal_operation: raise
        return response(e.status,{'error':e.message},cid)
    except Exception:
        log.exception('Milestone6 handler failure correlationId=%s',cid)
        if internal_operation: raise
        return response(500,{'error':'Internal server error'},cid)
