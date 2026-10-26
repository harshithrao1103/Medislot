from auth_common import register

def lambda_handler(event, context):
    return register(event, 'doctor')
