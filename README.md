# lambda-client-summary-notifications
Lambda function to send the client summary notification

* Listen SNS event to send the transactions summary of the given client id
* Get transactions from database and calculates totals and averages per month
* Save email template into public S3
* Send email

## Environment variables needed
* RDS_HOST
* RDS_USERNAME
* RDS_PASSWORD
* RDS_DATABASE

## Tables needed in database
* clients
  * id
  * name
  * email
* transactions
  * id
  * client_id
  * transaction_id
  * date
  * amount

