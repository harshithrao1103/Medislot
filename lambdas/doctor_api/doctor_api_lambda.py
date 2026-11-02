"""Milestone 3 doctor-only API. Authorization identity always comes from Lambda authorizer."""
import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
import boto3
from doctor_common import *

PHOTO_TYPES = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}
CONSULTS = {'Quick Consult': (15, 1, 1), 'Standard Consult': (30, 1, 1),
            'Detailed Consult': (60, 1, 1), 'Health Camp Session': (60, 2, 10)}

def validate_consult(data):
    name = require_text(data.get('name'), 'name', 100)
    if name not in CONSULTS:
        raise ApiError(400, 'Unsupported consultation type')
    duration, minimum, maximum = CONSULTS[name]
    if type(data.get('duration_minutes')) is not int or data['duration_minutes'] != duration:
        raise ApiError(400, f'{name} duration must be {duration} minutes')
    capacity = data.get('capacity')
    if type(capacity) is not int or not minimum <= capacity <= maximum:
        raise ApiError(400, f'{name} capacity must be {minimum}-{maximum}')
    try:
        fee = Decimal(str(data.get('fee')))
        if not fee.is_finite() or fee < 0 or fee > Decimal('99999999.99') or fee.as_tuple().exponent < -2:
            raise ValueError()
    except (InvalidOperation, ValueError, TypeError):
        raise ApiError(400, 'fee must be a nonnegative amount with up to two decimals')
    return name, duration, str(fee), capacity

def fetch_doctor(cur, uid):
    cur.execute("""SELECT u.user_id,u.name,u.email,d.licence_number,d.specialization,
       d.experience_years,d.qualifications,d.hospital_name,d.clinic_address,d.city,
       d.languages,d.timezone,d.photo_key FROM users u JOIN doctors d ON d.user_id=u.user_id
       WHERE u.user_id=%s""", (uid,))
    item = cur.fetchone()
    if not item:
        raise ApiError(404, 'Doctor profile not found')
    if isinstance(item['languages'], str):
        item['languages'] = json.loads(item['languages'])
    return item

def profile(event, uid, method, correlation):
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            if method == 'GET':
                return respond(200, fetch_doctor(cur, uid), correlation)
            data = body_json(event)
            allowed = {'name','specialization','experience_years','qualifications',
                       'hospital_name','clinic_address','city','languages','timezone'}
            if not data or set(data) - allowed:
                raise ApiError(400, 'Unsupported or empty profile update')
            fields = {}
            for field, limit in [('name',150),('specialization',100),('qualifications',65535),
                                 ('hospital_name',200),('clinic_address',500),('city',100)]:
                if field in data:
                    fields[field] = require_text(data[field], field, limit)
            if 'experience_years' in data:
                years = data['experience_years']
                if type(years) is not int or not 0 <= years <= 80:
                    raise ApiError(400, 'Invalid experience_years')
                fields['experience_years'] = years
            if 'languages' in data:
                languages = data['languages']
                if not isinstance(languages, list) or not languages or len(languages) > 20 or any(
                    not isinstance(x,str) or not x.strip() or len(x)>50 for x in languages):
                    raise ApiError(400, 'languages must be a nonempty list of language names')
                fields['languages'] = json.dumps(languages)
            if 'timezone' in data:
                if data['timezone'] != 'Asia/Kolkata':
                    raise ApiError(400, 'Milestone 3 supports Asia/Kolkata only')
                fields['timezone'] = data['timezone']
            fetch_doctor(cur, uid)
            if 'name' in fields:
                cur.execute('UPDATE users SET name=%s WHERE user_id=%s', (fields.pop('name'), uid))
            if fields:
                cur.execute('UPDATE doctors SET '+', '.join(f'{key}=%s' for key in fields)+
                            ' WHERE user_id=%s', (*fields.values(), uid))
            conn.commit()
            return respond(200, fetch_doctor(cur, uid), correlation)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def photo(event, uid, correlation):
    data = body_json(event)
    mime = data.get('contentType')
    if mime not in PHOTO_TYPES:
        raise ApiError(400, 'Only image/jpeg, image/png, image/webp allowed')
    size = data.get('sizeBytes')
    if type(size) is not int or not 0 < size <= 5*1024*1024:
        raise ApiError(400, 'sizeBytes must be between 1 and 5242880')
    bucket = os.environ['PHOTO_BUCKET']
    key = f'doctors/{uid}/{uuid.uuid4().hex}.{PHOTO_TYPES[mime]}'
    s3 = boto3.client('s3')
    # Presigned POST enforces content length and MIME; bytes never pass through API Gateway.
    upload = s3.generate_presigned_post(Bucket=bucket, Key=key,
        Fields={'Content-Type': mime}, Conditions=[{'Content-Type': mime},
        ['content-length-range',1,5*1024*1024]], ExpiresIn=300)
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('UPDATE doctors SET photo_key=%s WHERE user_id=%s', (key,uid))
            if not cur.rowcount:
                raise ApiError(404,'Doctor not found')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return respond(200, {'upload':upload,'photoKey':key,'expiresIn':300,
        'note':'Upload using multipart/form-data with the returned fields; photo is private.'}, correlation)

def consultation(event, uid, method, item_id, correlation):
    conn=db_connection()
    try:
        with conn.cursor() as cur:
            if method=='GET':
                cur.execute('SELECT * FROM consultation_types WHERE doctor_id=%s ORDER BY consultation_type_id', (uid,))
                return respond(200, {'items':cur.fetchall()}, correlation)
            if method in ('POST','PUT'):
                data=body_json(event)
                name,duration,fee,capacity=validate_consult(data)
                if method=='POST':
                    cur.execute('INSERT INTO consultation_types (doctor_id,name,duration_minutes,fee,capacity) VALUES (%s,%s,%s,%s,%s)',
                                (uid,name,duration,fee,capacity))
                    item_id=cur.lastrowid
                else:
                    cur.execute('UPDATE consultation_types SET name=%s,duration_minutes=%s,fee=%s,capacity=%s WHERE doctor_id=%s AND consultation_type_id=%s',
                                (name,duration,fee,capacity,uid,item_id))
                    if not cur.rowcount:
                        raise ApiError(404,'Consultation type not found')
                conn.commit()
                return respond(200 if method=='PUT' else 201, {'consultationTypeId':item_id},correlation)
            if method=='DELETE':
                cur.execute('DELETE FROM consultation_types WHERE doctor_id=%s AND consultation_type_id=%s',(uid,item_id))
                if not cur.rowcount:
                    raise ApiError(404,'Consultation type not found')
                conn.commit()
                return respond(200,{'deleted':True},correlation)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def availability(event,uid,method,correlation):
    conn=db_connection()
    try:
        with conn.cursor() as cur:
            if method=='GET':
                cur.execute("""SELECT availability_id,consultation_type_id,day_of_week,start_time,end_time
                    FROM doctor_availability WHERE doctor_id=%s ORDER BY day_of_week,start_time""",(uid,))
                return respond(200,{'items':cur.fetchall()},correlation)
            data=body_json(event)
            patterns=data.get('patterns')
            if not isinstance(patterns,list) or len(patterns)>100:
                raise ApiError(400,'patterns must be a list of at most 100 entries')
            normalized=[]
            for item in patterns:
                if not isinstance(item,dict):
                    raise ApiError(400,'Invalid pattern')
                day=item.get('day_of_week')
                typ=item.get('consultation_type_id')
                if type(day) is not int or not 0<=day<=6 or type(typ) is not int:
                    raise ApiError(400,'Invalid weekday or consultation type')
                start=parse_time(item.get('start_time')); end=parse_time(item.get('end_time'))
                if start>=end:
                    raise ApiError(400,'start_time must precede end_time')
                normalized.append((uid,typ,day,start.strftime('%H:%M'),end.strftime('%H:%M')))
            # Reject overlapping windows even when consultation types differ.
            for i,a in enumerate(normalized):
                for b in normalized[i+1:]:
                    if a[2]==b[2] and a[3]<b[4] and b[3]<a[4]:
                        raise ApiError(400,'Overlapping availability patterns')
            cur.execute('SELECT consultation_type_id FROM consultation_types WHERE doctor_id=%s',(uid,))
            owned={r['consultation_type_id'] for r in cur.fetchall()}
            if any(x[1] not in owned for x in normalized):
                raise ApiError(403,'Consultation type does not belong to doctor')
            cur.execute('DELETE FROM doctor_availability WHERE doctor_id=%s',(uid,))
            if normalized:
                cur.executemany("""INSERT INTO doctor_availability
                   (doctor_id,consultation_type_id,day_of_week,start_time,end_time)
                   VALUES (%s,%s,%s,%s,%s)""",normalized)
            conn.commit()
            return respond(200,{'updated':len(normalized),'materialisation':'Next scheduled run or manual invocation'},correlation)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def blackouts(event,uid,method,item_id,correlation):
    conn=db_connection()
    try:
        with conn.cursor() as cur:
            if method=='GET':
                cur.execute('SELECT blackout_id,blackout_date,reason FROM doctor_blackouts WHERE doctor_id=%s ORDER BY blackout_date',(uid,))
                return respond(200,{'items':cur.fetchall()},correlation)
            if method=='POST':
                data=body_json(event)
                day=parse_date(data.get('blackout_date'))
                reason=data.get('reason')
                if reason is not None and (not isinstance(reason,str) or len(reason)>255):
                    raise ApiError(400,'Invalid reason')
                cur.execute('SELECT blackout_id FROM doctor_blackouts WHERE doctor_id=%s AND blackout_date=%s',(uid,day))
                if cur.fetchone():
                    raise ApiError(409,'Blackout already exists')
                cur.execute('INSERT INTO doctor_blackouts (doctor_id,blackout_date,reason) VALUES (%s,%s,%s)',(uid,day,reason))
                conn.commit()
                return respond(201,{'blackoutId':cur.lastrowid},correlation)
            if method=='DELETE':
                cur.execute('DELETE FROM doctor_blackouts WHERE doctor_id=%s AND blackout_id=%s',(uid,item_id))
                if not cur.rowcount:
                    raise ApiError(404,'Blackout not found')
                conn.commit()
                return respond(200,{'deleted':True},correlation)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def schedule(event,uid,correlation):
    day=parse_date((event.get('queryStringParameters') or {}).get('date'))
    import datetime as dt
    from zoneinfo import ZoneInfo
    from boto3.dynamodb.conditions import Key
    start=dt.datetime.combine(day,dt.time.min,tzinfo=ZoneInfo('Asia/Kolkata')).astimezone(dt.timezone.utc)
    end=(dt.datetime.combine(day+dt.timedelta(days=1),dt.time.min,tzinfo=ZoneInfo('Asia/Kolkata'))).astimezone(dt.timezone.utc)
    table=boto3.resource('dynamodb').Table(os.environ['SLOTS_TABLE'])
    params={'IndexName':'DoctorStartIndex','KeyConditionExpression':Key('doctorId').eq(str(uid)) &
            Key('startTimeUTC').between(start.isoformat(),end.isoformat()),'Limit':100}
    items=[]
    while True:
        res=table.query(**params)
        items.extend(x for x in res['Items'] if x['startTimeUTC']<end.isoformat() and x.get('status')=='AVAILABLE')
        if 'LastEvaluatedKey' not in res:break
        params['ExclusiveStartKey']=res['LastEvaluatedKey']
    conn=db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT a.appointment_id,a.slot_id,a.status,a.start_time_utc,
                u.user_id AS patient_id,u.name AS patient_name FROM appointments a
                JOIN users u ON u.user_id=a.patient_id
                WHERE a.doctor_id=%s AND a.start_time_utc >= %s AND a.start_time_utc < %s
                ORDER BY a.start_time_utc""",(uid,start.replace(tzinfo=None),end.replace(tzinfo=None)))
            appointments=cur.fetchall()
    finally:conn.close()
    by_slot={}
    for a in appointments:by_slot.setdefault(a['slot_id'],[]).append(a)
    for slot in items:slot['appointments']=by_slot.get(slot['slotId'],[])
    return respond(200,{'date':day.isoformat(),'timezone':'Asia/Kolkata','slots':items},correlation)

def lambda_handler(event,context):
    correlation=cid(event)
    try:
        uid=doctor_id(event)
        method=event.get('httpMethod','')
        resource=event.get('resource','')
        raw_id=(event.get('pathParameters') or {}).get('id')
        item_id=None
        if raw_id is not None:
            if not str(raw_id).isdigit() or int(raw_id)<1:
                raise ApiError(400,'Invalid record ID')
            item_id=int(raw_id)
        if resource=='/doctors/me':
            return profile(event,uid,method,correlation)
        if resource=='/doctors/me/photo' and method=='POST':
            return photo(event,uid,correlation)
        if resource.startswith('/doctors/me/consultation-types'):
            return consultation(event,uid,method,item_id,correlation)
        if resource=='/doctors/me/availability':
            return availability(event,uid,method,correlation)
        if resource.startswith('/doctors/me/blackouts'):
            return blackouts(event,uid,method,item_id,correlation)
        if resource=='/doctors/me/schedule' and method=='GET':
            return schedule(event,uid,correlation)
        raise ApiError(404,'Route not found')
    except Exception as exc:
        return dispatch_error(exc,correlation)
