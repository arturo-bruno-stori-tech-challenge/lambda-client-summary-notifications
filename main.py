import os
import json
import uuid
import boto3
import pymysql
from io import StringIO
from pathlib import Path
from jinja2 import Environment, BaseLoader


CHARSET = 'UTF-8'
AWS_REGION = os.getenv('AWS_REGION', 'us-east-2')
SENDER = os.getenv('SENDER', 'abrunocarrillo@gmail.com')
PUBLIC_BUCKET = os.getenv('S3_TRANSACTIONS_SUMMARY_HTMLS_BUCKET')
rds_host = os.getenv('RDS_HOST')
rds_username = os.getenv('RDS_USERNAME')
rds_password = os.getenv('RDS_PASSWORD')
rds_database = os.getenv('RDS_DATABASE')

s3 = boto3.client('s3')
ses = boto3.client('ses', region_name=AWS_REGION)


try:
    db = pymysql.connect(
        host=rds_host,
        user=rds_username,
        passwd=rds_password,
        db=rds_database,
        connect_timeout=5,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
except pymysql.MySQLError as e:
    print(f'ERROR: Unexpected error: Could not connect to MySQL instance: "{e}"')
    exit(1)


def get_client(client_id: int):
    print(f'Looking for client with ID: "{client_id}"')
    with db.cursor() as cursor:
        cursor.execute('SELECT * FROM clients WHERE id=%s', client_id)
        if client := cursor.fetchone():
            print(f'Client found: "{client}"')
            return client
    return None


def get_client_transactions(client_id: int):
    print(f'Looking for client #"{client_id}" transactions')
    with db.cursor() as cursor:
        cursor.execute('SELECT * FROM transactions WHERE client_id=%s ORDER BY date ASC', client_id)
        if transactions := cursor.fetchall():
            print(f'Found "{len(transactions)}" client transactions')
            return transactions
    return None


def calculate_plain_text_summary(client_name, transactions: dict):
    summary = f'Hi {client_name}\nHope you are doing great!\nPlease find your transactions summary below'
    summary += "Total balance: ${:,.2f}\nAverage debit: $-{:,.2f}\nAverage credit: ${:,.2f}".format(
        transactions['total_balance'],
        transactions['averages']['debit'],
        transactions['averages']['credit']
    )
    return summary


def save_to_s3(bucket: str, file_content: str):
    print(f'Saving email notification to public S3 bucket {bucket}')
    file = StringIO(file_content)
    filename = f'{uuid.uuid4()}.html'
    expiration = 12 * 24 * 60  # 12 hours
    s3.put_object(Body=file.getvalue(), Bucket=bucket, Key=filename)

    print(f'Getting public url for {bucket}/{filename}')
    return s3.generate_presigned_url(
        'get_object',
        Params={
            'Key': filename,
            'Bucket': bucket,
            'ResponseContentType': 'text/html',
            'ResponseContentDisposition': 'inline'
        },
        ExpiresIn=expiration
    )


def send_email(client: dict, transactions: dict):
    subject = 'Transactions summary'
    recipient = SENDER  # client['email'] For demo purpose, send the email to me

    email_template = Path(Path(__file__).parent, 'email_summary.html')
    template = Environment(loader=BaseLoader()).from_string(email_template.read_text())
    html_text = template.render(**transactions)

    # Save template for public S3
    transactions['email_public_url'] = save_to_s3(PUBLIC_BUCKET, html_text)

    # now, re-render the template with the public URL to send the email
    html_text = template.render(**transactions)
    plain_text = calculate_plain_text_summary(client['name'], transactions)

    # For local development
    if os.environ.get("AWS_EXECUTION_ENV") is not None:
        parsed = Path(Path(__file__).parent, f'{client["name"].replace(" ", "_")}-summary-email.html')
        parsed.write_text(html_text)

    try:
        response = ses.send_email(
            Destination={
                'ToAddresses': [
                    recipient,
                ],
            },
            Message={
                'Body': {
                    'Html': {
                        'Charset': CHARSET,
                        'Data': html_text,
                    },
                    'Text': {
                        'Charset': CHARSET,
                        'Data': plain_text,
                    },
                },
                'Subject': {
                    'Charset': CHARSET,
                    'Data': subject,
                },
            },
            Source=SENDER
        )
    except Exception as e:
        print('Could not send email', e)
    else:
        print("Email sent! Message ID:", response['MessageId'])


def lambda_handler(event, context):
    print(f'Received event: {json.dumps(event)}')
    sns_message = json.loads(event['Records'][0]['Sns']['Message'])

    client_id = sns_message['client_id']
    client = get_client(client_id)
    client_transactions = get_client_transactions(client['id'])
    transactions = {
        'client_name': client['name'],
        'total_balance': 0,
        'averages': {
            'credit': 0,
            'debit': 0
        },
        'months': {}
    }
    debit = []
    credit = []

    for transaction in client_transactions:
        month = transaction['date'].strftime('%B %Y')
        amount = float(transaction['amount'][1:])

        if month not in transactions['months']:
            transactions['months'][month] = {'credit': [], 'debit': []}

        if transaction['amount'].startswith('+'):
            credit.append(amount)
            transactions['total_balance'] += amount
            transactions['months'][month]['credit'].append(amount)
        elif transaction['amount'].startswith('-'):
            debit.append(amount)
            transactions['total_balance'] -= amount
            transactions['months'][month]['debit'].append(amount)

    transactions['averages']['debit'] = sum(debit) / len(debit)
    transactions['averages']['credit'] = sum(credit) / len(credit)

    print(
        json.dumps(
            {
                'total_balance': transactions['total_balance'],
                'averages': transactions['averages']
            },
            indent=4
        )
    )

    send_email(client, transactions)

    # Finish function successfully
    message = f'Transactions summary for client "{client}" successfully sent!'
    return {
        'message': message
    }


if __name__ == '__main__':
    lambda_handler({'client_id': 5}, None)
