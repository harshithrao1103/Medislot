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

        # 5. Execute SQL statements
        with connection.cursor() as cursor:

            for statement in statements:
                cursor.execute(statement)

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