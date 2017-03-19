// AWS Lambda Function: StreamAlert Processor
//    Matches rules against logs from Kinesis Streams or S3
resource "aws_lambda_function" "streamalert_rule_processor" {
  function_name = "${var.prefix}_${var.cluster}_streamalert_rule_processor"
  description   = "StreamAlert Rule Processor"
  runtime       = "python2.7"
  role          = "${aws_iam_role.streamalert_rule_processor_role.arn}"
  handler       = "${lookup(var.rule_processor_config, "handler")}"
  memory_size   = "${element(var.rule_processor_lambda_config["${var.cluster}"], 1)}"
  timeout       = "${element(var.rule_processor_lambda_config["${var.cluster}"], 0)}"
  s3_bucket     = "${lookup(var.rule_processor_config, "source_bucket")}"
  s3_key        = "${lookup(var.rule_processor_config, "source_object_key")}"
  publish       = true
}

// StreamAlert Processor Production Alias
resource "aws_lambda_alias" "rule_processor_production" {
  name             = "production"
  description      = "Production StreamAlert Rule Processor Alias"
  function_name    = "${aws_lambda_function.streamalert_rule_processor.arn}"
  function_version = "${aws_lambda_function.streamalert_rule_processor.version}"
}

// AWS Lambda Function: StreamAlert Alert Processor
//    Send alerts to declared outputs
resource "aws_lambda_function" "streamalert_alert_processor" {
  function_name = "${var.prefix}_${var.cluster}_streamalert_alert_processor"
  description   = "StreamAlert Alert Processor"
  runtime       = "python2.7"
  role          = "${aws_iam_role.streamalert_alert_processor_role.arn}"
  handler       = "${lookup(var.alert_processor_config, "handler")}"
  memory_size   = "${element(var.alert_processor_lambda_config["${var.cluster}"], 1)}"
  timeout       = "${element(var.alert_processor_lambda_config["${var.cluster}"], 0)}"
  s3_bucket     = "${lookup(var.alert_processor_config, "source_bucket")}"
  s3_key        = "${lookup(var.alert_processor_config, "source_object_key")}"
  publish       = true
}

// StreamAlert Output Processor Production Alias
resource "aws_lambda_alias" "alert_processor_production" {
  name             = "production"
  description      = "Production StreamAlert Alert Processor Alias"
  function_name    = "${aws_lambda_function.streamalert_alert_processor.arn}"
  function_version = "${aws_lambda_function.streamalert_alert_processor.version}"
}

// Allow SNS to invoke the StreamAlert Output Processor
resource "aws_lambda_permission" "with_sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = "${aws_lambda_function.streamalert_alert_processor.arn}"
  principal     = "sns.amazonaws.com"
  source_arn    = "${aws_sns_topic.streamalert.arn}"
}

// S3 bucket for S3 outputs
resource "aws_s3_bucket" "stream_alert_output" {
  bucket        = "${var.prefix}_${var.cluster}_streamalerts"
  acl           = "private"
  force_destroy = false
}
