import os
import json
import boto3
import pymysql
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):

    connection = None

    try:
        # 1. Read environment variables
        db_host = os.environ["DB_HOST"]
        db_name = os.environ["DB_NAME"]
        secret_arn = os.environ["DB_SECRET_ARN"]

        # 2. Retrieve credentials from Secrets Manager
        secrets_client = boto3.client("secretsmanager")

        response = secrets_client.get_secret_value(
            SecretId=secret_arn
        )

        credentials = json.loads(response["SecretString"])

        db_username = credentials["username"]
        db_password = credentials["password"]

        # 3. Connect to RDS MySQL
        connection = pymysql.connect(
            host=db_host,
            user=db_username,
            password=db_password,
            database=db_name,
            port=int(os.environ.get("DB_PORT", "3306")),
            connect_timeout=10,
            autocommit=True
        )

        logger.info("Successfully connected to RDS MySQL")

        # 4. Read schema.sql
        schema_path = os.path.join(
            os.path.dirname(__file__),
            "schema.sql"
        )

        with open(schema_path, "r", encoding="utf-8") as file:
            sql_script = file.read()

        # Remove SQL comments and split statements
        sql_lines = []

        for line in sql_script.splitlines():
            stripped = line.strip()

            if stripped and not stripped.startswith("--"):
                sql_lines.append(line)

        sql_script = "\n".join(sql_lines)

        statements = [
            statement.strip()
            for statement in sql_script.split(";")
            if statement.strip()
        ]

        # Fail closed on an existing incompatible schema. CREATE TABLE IF NOT EXISTS
        # cannot change old column layouts; never drop production tables automatically.
        expected = {
            'users': {'user_id', 'name', 'email', 'password_hash', 'role', 'created_at'},
            'specializations': {'specialization_id', 'specialization_name'},
            'doctors': {'user_id', 'licence_number', 'specialization_id', 'experience_years', 'qualifications', 'hospital_name', 'clinic_address', 'city', 'languages', 'timezone', 'photo_key'},
            'patients': {'user_id', 'date_of_birth', 'gender', 'phone', 'city', 'emergency_contact', 'notes'},
            'consultation_types': {'consultation_type_id', 'doctor_id', 'name', 'duration_minutes', 'fee', 'capacity'},
            'doctor_availability': {'availability_id', 'doctor_id', 'consultation_type_id', 'day_of_week', 'start_time', 'end_time'},
            'doctor_blackouts': {'blackout_id', 'doctor_id', 'blackout_date', 'reason'},
            'appointments': {'appointment_id', 'patient_id', 'doctor_id', 'slot_id', 'start_time_utc', 'status', 'fee', 'reschedule_count', 'created_at'},
            'refund_ledger': {'refund_id', 'appointment_id', 'refund_percentage', 'refund_amount', 'deduction_amount', 'reason', 'created_at'},
        }
        with connection.cursor() as cursor:
            cursor.execute('SHOW TABLES')
            existing = {row[0] for row in cursor.fetchall()}
            for table in existing.intersection(expected):
                cursor.execute(f'SHOW COLUMNS FROM `{table}`')
                actual_columns = {row[0] for row in cursor.fetchall()}
                if actual_columns != expected[table]:
                    raise ValueError(
                        f'Existing {table} schema differs from finalized design. '
                        'Back up and migrate/reset explicitly before initialization.'
                    )

        # 5. Execute SQL statements
        with connection.cursor() as cursor:

            for statement in statements:
                cursor.execute(statement)

        # Idempotent additive Milestone 4 indexes for existing databases.
        with connection.cursor() as cursor:
            for table, index_name, columns in (
                ('users','idx_users_name','name'),
                ('doctors','idx_doctors_hospital_city','hospital_name,city'),
                ('doctors','idx_doctors_city_specialization','city,specialization_id'),
            ):
                cursor.execute(f'SHOW INDEX FROM `{table}` WHERE Key_name=%s',(index_name,))
                if not cursor.fetchone():
                    cursor.execute(f'CREATE INDEX `{index_name}` ON `{table}` ({columns})')
        logger.info("Database schema initialized successfully")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "MediSlot database initialized successfully"
            })
        }

    except Exception as error:

        logger.exception("Database initialization failed")

        return {
            "statusCode": 500,
            "body": json.dumps({
                "message": "Database initialization failed",
                "error": str(error)
            })
        }

    finally:
        if connection:
            connection.close()