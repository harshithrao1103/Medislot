from auth_common import register, db_connection, response, cid
import json

def lambda_handler(event, context):
    if event.get('httpMethod') == 'GET' and event.get('resource') == '/specializations':
        conn = None
        try:
            conn = db_connection()
            with conn.cursor() as cur:
                cur.execute('SELECT specialization_id, specialization_name FROM specializations ORDER BY specialization_name')
                items = cur.fetchall()
            return response(200, {'specializations': items}, cid(event))
        except Exception:
            return response(503, {'error': 'Unable to load specializations'}, cid(event))
        finally:
            if conn is not None:
                conn.close()
    return register(event, 'doctor')
