# -----
# waf request limit 100/minutes + Managed Rules (SQLi & XSS)
# -----
resource "aws_wafv2_web_acl" "web_rate_limit" {
  name        = "${local.name_prefix}-rate-limit"
  description = "WAF rate base rule and managed security rules for access web"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # send metric to cloudwatch
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name_prefix}_waf_acl"
    sampled_requests_enabled   = true
  }

  # ---------------------------------------------------------
  # Rule 1: Rate Limit (100 req/min | GET)
  # ---------------------------------------------------------
  rule {
    name     = "web_rate"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = 100
        aggregate_key_type    = "IP"
        evaluation_window_sec = 60

        scope_down_statement {
          byte_match_statement {
            search_string         = "GET"
            positional_constraint = "EXACTLY"

            field_to_match {
              method {}
            }

            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "web_rate_metric"
      sampled_requests_enabled   = true
    }
  }

  # ---------------------------------------------------------
  # Rule 2: SQL Injection
  # ---------------------------------------------------------
  rule {
    name     = "AWS-AWSManagedRulesSQLiRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesSQLiRuleSetMetric"
      sampled_requests_enabled   = true
    }
  }

  # ---------------------------------------------------------
  # Rule 3: XSS, LFI 
  # ---------------------------------------------------------
  rule {
    name     = "AWS-AWSManagedRulesCommonRuleSet"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWSManagedRulesCommonRuleSetMetric"
      sampled_requests_enabled   = true
    }
  }
}

# -----
# associate WAF to ALB
# -----
resource "aws_wafv2_web_acl_association" "alb_asscoc" {
  resource_arn = aws_lb.lb.arn
  web_acl_arn  = aws_wafv2_web_acl.web_rate_limit.arn
}
