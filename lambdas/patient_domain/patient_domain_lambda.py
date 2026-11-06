"""Milestone 4 patient CRUD and public doctor discovery. No booking writes."""
import datetime as dt
import json
import os
import re
from decimal import Decimal
from zoneinfo import ZoneInfo
import boto3
from boto3.dynamodb.conditions import Key
from auth_common import db_connection, body_json, cid, response

class BadRequest(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

def result(status, data, correlation):
    return response(status, data, correlation)

def positive(value, name):
    try:
        n = int(value)
        if n < 1 or isinstance(value, bool): raise ValueError()
        return n
    except (TypeError, ValueError):
        raise BadRequest(400, 'Invalid ' + name)

def text(value, name, limit, optional=False):
    if optional and value in (None, ''): return ''
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise BadRequest(400, 'Invalid ' + name)
    return value.strip()

def patient_identity(event):
    ctx = (event.get('requestContext') or {}).get('authorizer') or {}
    if str(ctx.get('role', '')).lower() != 'patient':
        raise BadRequest(403, 'Patient role required')
    return positive(ctx.get('userId'), 'authorized identity')

def patient_profile(event, method, correlation):
    uid = patient_identity(event)  # NEVER accept a patient ID from request body/path.
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            if method == 'GET':
                cur.execute("""SELECT u.user_id,u.name,u.email,p.date_of_birth,p.gender,p.phone,p.city,
                    p.emergency_contact,p.notes FROM users u JOIN patients p ON p.user_id=u.user_id
                    WHERE u.user_id=%s AND u.role='PATIENT' """, (uid,))
                item = cur.fetchone()
                if not item: raise BadRequest(404, 'Patient not found')
                item['date_of_birth'] = item['date_of_birth'].isoformat()
                return result(200, item, correlation)
            if method == 'PUT':
                data = body_json(event)
                if not isinstance(data, dict): raise BadRequest(400, 'Invalid JSON object')
                allowed = {'name','date_of_birth','gender','phone','city','emergency_contact','notes'}
                if not data or any(k not in allowed for k in data):
                    raise BadRequest(400, 'Invalid profile fields')
                if 'name' in data:
                    cur.execute("UPDATE users SET name=%s WHERE user_id=%s AND role='PATIENT'", (text(data['name'],'name',100),uid))
                updates = []
                args = []
                for field,limit in [('gender',30),('phone',20),('city',100),('emergency_contact',100),('notes',2000)]:
                    if field in data:
                        updates.append(field+'=%s')
                        args.append(text(data[field],field,limit,optional=field=='notes'))
                if 'date_of_birth' in data:
                    try:
                        dob = dt.date.fromisoformat(data['date_of_birth'])
                        if dob > dt.date.today(): raise ValueError()
                    except (TypeError,ValueError): raise BadRequest(400,'Invalid date_of_birth')
                    updates.append('date_of_birth=%s');args.append(dob)
                if updates:
                    cur.execute('UPDATE patients SET '+','.join(updates)+' WHERE user_id=%s', (*args,uid))
                conn.commit()
                return result(200, {'message':'Profile updated'}, correlation)
            if method == 'DELETE':
                # Refuse deletion when appointments exist; never orphan bookings.
                cur.execute('SELECT 1 FROM appointments WHERE patient_id=%s LIMIT 1',(uid,))
                if cur.fetchone(): raise BadRequest(409,'Profile has appointments; contact support')
                cur.execute("DELETE FROM users WHERE user_id=%s AND role='PATIENT'",(uid,))
                if not cur.rowcount: raise BadRequest(404,'Patient not found')
                conn.commit()
                return result(200, {'message':'Profile deleted; please sign out'},correlation)
        raise BadRequest(405,'Method not allowed')
    except Exception:
        conn.rollback()
        raise
    finally: conn.close()

def search(event, correlation):
    q = event.get('queryStringParameters') or {}
    page = positive(q.get('page',1),'page')
    size = positive(q.get('page_size',10),'page_size')
    if size > 50 or page > 10000: raise BadRequest(400,'Pagination limit exceeded')
    clauses = []
    args = []
    for field,sql in [('name','u.name'),('hospital','d.hospital_name'),('city','d.city')]:
        if q.get(field):
            value = text(q[field],field,100)
            clauses.append(sql+' LIKE %s')
            args.append('%'+value.replace(chr(92),chr(92)*2).replace('%',chr(92)+'%').replace('_',chr(92)+'_')+'%')
    if q.get('specialization'):
        value=text(q['specialization'],'specialization',100)
        clauses.append('s.specialization_name LIKE %s')
        args.append('%'+value.replace(chr(92),chr(92)*2).replace('%',chr(92)+'%').replace('_',chr(92)+'_')+'%')
    where = ' WHERE '+' AND '.join(clauses) if clauses else ''
    frm = ' FROM doctors d JOIN users u ON u.user_id=d.user_id JOIN specializations s ON s.specialization_id=d.specialization_id '
    conn=db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) AS total'+frm+where,tuple(args))
            total=cur.fetchone()['total']
            cur.execute("""SELECT d.user_id AS doctor_id,u.name,d.hospital_name,d.city,
                s.specialization_name AS specialization,d.experience_years,d.qualifications"""+frm+where+
                ' ORDER BY u.name,u.user_id LIMIT %s OFFSET %s',(*args,size,(page-1)*size))
            items=cur.fetchall()
        return result(200,{'items':items,'page':page,'page_size':size,'total':total,'total_pages':(total+size-1)//size},correlation)
    finally: conn.close()

def public_doctor(doctor_id,correlation):
    conn=db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT d.user_id AS doctor_id,u.name,d.hospital_name,d.clinic_address,
                d.city,d.experience_years,d.qualifications,d.languages,d.timezone,
                s.specialization_name AS specialization
                FROM doctors d JOIN users u ON u.user_id=d.user_id
                JOIN specializations s ON s.specialization_id=d.specialization_id WHERE d.user_id=%s""",(doctor_id,))
            doctor=cur.fetchone()
            if not doctor: raise BadRequest(404,'Doctor not found')
            if isinstance(doctor['languages'],str):doctor['languages']=json.loads(doctor['languages'])
            cur.execute("""SELECT consultation_type_id,name,duration_minutes,fee,capacity
                FROM consultation_types WHERE doctor_id=%s ORDER BY consultation_type_id""",(doctor_id,))
            consultations=cur.fetchall()
            for c in consultations: c['fee']=str(c['fee'])
            doctor['consultation_types']=consultations
            return result(200,doctor,correlation)
    finally:conn.close()

def slots(doctor_id,event,correlation):
    q=event.get('queryStringParameters') or {}
    try:
        start=dt.date.fromisoformat(q['start_date'])
        end=dt.date.fromisoformat(q['end_date'])
    except (KeyError,ValueError,TypeError): raise BadRequest(400,'start_date and end_date must be YYYY-MM-DD')
    if end<start or (end-start).days>30: raise BadRequest(400,'Date range must be at most 30 days')
    if start<dt.datetime.now(ZoneInfo('Asia/Kolkata')).date(): raise BadRequest(400,'Past dates are not available')
    consultation=positive(q['consultation_type_id'],'consultation_type_id') if q.get('consultation_type_id') else None
    # A doctor must exist; do not disclose arbitrary DynamoDB identifiers.
    conn=db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM doctors WHERE user_id=%s',(doctor_id,))
            if not cur.fetchone():raise BadRequest(404,'Doctor not found')
    finally:conn.close()
    tz=ZoneInfo('Asia/Kolkata')
    first=dt.datetime.combine(start,dt.time.min,tzinfo=tz).astimezone(dt.timezone.utc).isoformat()
    last=dt.datetime.combine(end+dt.timedelta(days=1),dt.time.min,tzinfo=tz).astimezone(dt.timezone.utc).isoformat()
    table=boto3.resource('dynamodb').Table(os.environ['SLOTS_TABLE'])
    params={'IndexName':'DoctorStartIndex','KeyConditionExpression':Key('doctorId').eq(str(doctor_id)) & Key('startTimeUTC').between(first,last),'Limit':100}
    items=[]
    while True:
        data=table.query(**params)
        for s in data.get('Items',[]):
            if s.get('status') not in ('AVAILABLE','ACTIVE'):continue
            if consultation is not None and str(s.get('consultationTypeId'))!=str(consultation):continue
            start_time=dt.datetime.fromisoformat(s['startTimeUTC'].replace('Z','+00:00'))
            if start_time.astimezone(dt.timezone.utc)<=dt.datetime.now(dt.timezone.utc):continue
            if not start<=start_time.astimezone(tz).date()<=end:continue
            free=max(0,int(s.get('capacity',0))-int(s.get('bookedCount',0))-int(s.get('heldCount',0)))
            if free<=0:continue
            items.append({'slotId':s['slotId'],'consultationTypeId':s['consultationTypeId'],
                'startTimeUTC':s['startTimeUTC'],'endTimeUTC':s['endTimeUTC'],
                'capacity':int(s['capacity']),'bookedCount':int(s.get('bookedCount',0)),
                'heldCount':int(s.get('heldCount',0)),'freeSeats':free})
        if 'LastEvaluatedKey' not in data:break
        params['ExclusiveStartKey']=data['LastEvaluatedKey']
    items.sort(key=lambda s:s['startTimeUTC'])
    return result(200,{'doctor_id':doctor_id,'slots':items},correlation)

def lambda_handler(event,context):
    correlation=cid(event)
    try:
        resource=event.get('resource','')
        method=event.get('httpMethod','')
        if resource=='/patients/me' and method in ('GET','PUT','DELETE'):
            return patient_profile(event,method,correlation)
        if resource=='/doctors' and method=='GET':return search(event,correlation)
        if resource=='/doctors/{doctorId}' and method=='GET':
            return public_doctor(positive((event.get('pathParameters') or {}).get('doctorId'),'doctorId'),correlation)
        if resource=='/doctors/{doctorId}/slots' and method=='GET':
            return slots(positive((event.get('pathParameters') or {}).get('doctorId'),'doctorId'),event,correlation)
        raise BadRequest(404,'Route not found')
    except BadRequest as exc:return result(exc.status,{'error':exc.message},correlation)
    except ValueError:return result(400,{'error':'Invalid request'},correlation)
    except Exception:
        import logging
        logging.exception('Patient domain request failed correlationId=%s',correlation)
        return result(500,{'error':'Internal server error'},correlation)
