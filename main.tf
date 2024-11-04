terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"  # Replace with your desired region
}

variable "model_name" {
  type        = string
  description = "Name of the Foundation Model to enhance the customer experience"
  default     = "Claude-3-Haiku"
  validation {
    condition     = contains(["Claude-3-Haiku", "Claude-3-Sonnet"], var.model_name)
    error_message = "Allowed values for model_name are 'Claude-3-Haiku' or 'Claude-3-Sonnet'."
  }
}

locals {
  foundation_model_mapping = {
    "Claude-3-Haiku"  = "anthropic.claude-3-haiku-20240307-v1:0"
    "Claude-3-Sonnet" = "anthropic.claude-3-sonnet-20240229-v1:0"
  }
  selected_model = local.foundation_model_mapping[var.model_name]
}

resource "aws_iam_role" "bot_runtime_role" {
  name = "BotRuntimeRole"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = ["lexv2.amazonaws.com"]
        }
        Action = ["sts:AssumeRole"]
      }
    ]
  })

  inline_policy {
    name = "LexRuntimeRolePolicy"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect   = "Allow"
          Action   = ["polly:SynthesizeSpeech"]
          Resource = "*"
        },
        {
          Effect   = "Allow"
          Action   = ["lambda:invokeFunction"]
          Resource = aws_lambda_function.ai_assist_lambda.arn
        }
      ]
    })
  }
}

resource "aws_iam_role" "ai_assist_lambda_role" {
  name = "AIAssistLambdaRole"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = ["lambda.amazonaws.com"]
        }
        Action = ["sts:AssumeRole"]
      }
    ]
  })

  inline_policy {
    name = "BedrockModelInvokePolicy"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = ["bedrock:InvokeModel"]
          Resource = "arn:aws:bedrock:${data.aws_region.current.name}::foundation-model/${local.selected_model}"
        }
      ]
    })
  }

  managed_policy_arns = ["arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"]
}

resource "aws_iam_policy" "lex_bot_read_policy" {
  name = "LexBotReadPolicy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "lex:ListIntents",
          "lex:ListSlots",
          "lex:DescribeSlotType"
        ]
        Resource = "arn:aws:lex:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:bot/${aws_lex_bot.fnol_bot.id}"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lex_bot_read_policy_attachment" {
  policy_arn = aws_iam_policy.lex_bot_read_policy.arn
  role       = aws_iam_role.ai_assist_lambda_role.name
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_lambda_function" "ai_assist_lambda" {
  function_name = "ai-assist-lambda-${terraform.workspace}"
  handler       = "lambda_function.lambda_handler"
  role          = aws_iam_role.ai_assist_lambda_role.arn
  s3_bucket     = "aws-blogs-artifacts-public"
  s3_key        = "ML-17242/llm-assist-lambda.zip"
  runtime       = "python3.12"
  timeout       = 60
  reserved_concurrent_executions = 100

  environment {
    variables = {
      foundation_model = local.selected_model
    }
  }
}

resource "aws_lambda_permission" "lex_permission" {
  statement_id  = "AllowLexInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai_assist_lambda.function_name
  principal     = "lexv2.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn    = "arn:aws:lex:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:bot-alias/${aws_lex_bot.fnol_bot.id}/*"
}

resource "aws_lex_bot" "fnol_bot" {
  name = "FNOLBot"
  role_arn = aws_iam_role.bot_runtime_role.arn
  data_privacy {
    child_directed = false
  }
  idle_session_ttl_in_seconds = 300
  description = "This bot is responsible for gathering various FNOL data regarding claims"
  auto_build_bot_locales = false

  bot_locale {
    locale_id = "en_US"
    description = "Gather FNOL Locale"
    nlu_confidence_threshold = 0.40
    voice_settings {
      voice_id = "Ivy"
    }

    slot_type {
      name = "Damage"
      description = "Damage that occurred to the home"
      value_selection_setting {
        resolution_strategy = "TOP_RESOLUTION"
      }
      slot_type_value {
        sample_value {
          value = "water"
        }
        sample_value {
          value = "roof"
        }
        sample_value {
          value = "window"
        }
        sample_value {
          value = "plumbing"
        }
        sample_value {
          value = "tree"
        }
        sample_value {
          value = "bodily injury"
        }
        sample_value {
          value = "total loss"
        }
      }
    }

    slot_type {
      name = "PersonalInjury"
      description = "Injuries that may have occurred as part of the claim"
      value_selection_setting {
        resolution_strategy = "TOP_RESOLUTION"
      }
      slot_type_value {
        sample_value {
          value = "laceration"
        }
        sample_value {
          value = "bruise"
        }
        sample_value {
          value = "broken bone"
        }
        sample_value {
          value = "concussion"
        }
        sample_value {
          value = "cpr"
        }
      }
    }

    intent {
      name = "GatherFNOLInfo"
      description = "Intent for gathering FNOL of loss info"
      sample_utterances = [
        "I'd like to start a home claim",
        "I need to make a claim",
        "Claim",
        "claim for my {Damage}"
      ]
      dialog_code_hook {
        enabled = true
      }
      slot_priority {
        priority = 1
        slot_name = "Damage"
      }
      slot_priority {
        priority = 2
        slot_name = "PersonalInjury"
      }
      slot {
        name = "Damage"
        description = "Damage to the home"
        slot_type_name = "Damage"
        value_elicitation_setting {
          slot_constraint = "Required"
          prompt_specification {
            max_retries = 3
            message_groups {
              message {
                plain_text_message {
                  value = "What portion of the home was damaged?"
                }
              }
            }
            allow_interrupt = false
          }
        }
      }
      slot {
        name = "PersonalInjury"
        description = "Personal Injury during the claim"
        slot_type_name = "PersonalInjury"
        value_elicitation_setting {
          slot_constraint = "Required"
          prompt_specification {
            max_retries = 3
            message_groups {
              message {
                plain_text_message {
                  value = "Please describe any injuries that occurred during the incident."
                }
              }
            }
            allow_interrupt = false
          }
        }
      }
    }

    intent {
      name = "FallbackIntent"
      description = "Default intent when no other intent matches"
      parent_intent_signature = "AMAZON.FallbackIntent"
      dialog_code_hook {
        enabled = true
      }
    }
  }

  test_bot_alias_settings {
    bot_alias_locale_settings {
      locale_id = "en_US"
      bot_alias_locale_setting {
        enabled = true
        code_hook_specification {
          lambda_code_hook {
            code_hook_interface_version = "1.0"
            lambda_arn = aws_lambda_function.ai_assist_lambda.arn
          }
        }
      }
    }
  }
}

# Update the Lambda permission to use the correct bot ID
resource "aws_lambda_permission" "lex_permission" {
  statement_id  = "AllowLexInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai_assist_lambda.function_name
  principal     = "lexv2.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn    = "arn:aws:lex:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:bot-alias/${aws_lex_bot.fnol_bot.id}/*"
}

resource "aws_lex_bot_version" "fnol_bot_version" {
  bot_id = aws_lex_bot.fnol_bot.id
  description = "This bot is responsible for gathering various FNOL data regarding claims"
  bot_version_locale_specification {
    bot_version_locale_details {
      source_bot_version = "DRAFT"
    }
    locale_id = "en_US"
  }
}

resource "aws_lex_bot_alias" "fnol_bot_alias" {
  bot_alias_name = "FNOLBotAlias"
  bot_id         = aws_lex_bot.fnol_bot.id
  bot_version    = aws_lex_bot_version.fnol_bot_version.bot_version
  
  bot_alias_locale_settings {
    bot_alias_locale_setting {
      enabled = true
      code_hook_specification {
        lambda_code_hook {
          code_hook_interface_version = "1.0"
          lambda_arn = aws_lambda_function.ai_assist_lambda.arn
        }
      }
    }
    locale_id = "en_US"
  }
}

# Update the Lambda permission to use the correct bot alias ARN
resource "aws_lambda_permission" "lex_permission" {
  statement_id  = "AllowLexInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai_assist_lambda.function_name
  principal     = "lexv2.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn    = "${aws_lex_bot_alias.fnol_bot_alias.arn}/*"
}

