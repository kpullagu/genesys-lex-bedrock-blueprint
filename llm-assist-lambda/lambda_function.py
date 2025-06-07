import logging
import os
import re
from dialog_utils import (
    get_intents,
    get_slots,
    get_slot_values,
    get_next_unfilled_slot,
    set_slot,
    invoke_bedrock,
    extract_tag_content,
)

# Logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Constants
MODEL_ID = os.environ.get("foundation_model")
logger.info(f"Using foundation model: {MODEL_ID}")

# Mock claim status database
CLAIM_STATUSES = {
    "CLM-123456": "In Progress",
    "CLM-234567": "Under Review",
    "CLM-345678": "Completed",
    "CLM-456789": "Pending Documentation"
}

def validate_claim_number(claim_number):
    """Validate claim number format (CLM-XXXXXX)"""
    pattern = r'^CLM-\d{6}$'
    return bool(re.match(pattern, claim_number))

def get_claim_status(claim_number):
    """Get status for a claim number"""
    return CLAIM_STATUSES.get(claim_number, "Not Found")

def generate_status_response(claim_number, status):
    """Generate natural language response using LLM"""
    with open("claim_status_prompt.txt", "r") as file:
        status_prompt = file.read()
    
    llm_output = invoke_bedrock(
        status_prompt.format(
            claim_number=claim_number,
            status=status
        ),
        MODEL_ID,
    )
    
    response = extract_tag_content(llm_output, "status_response")
    confidence = extract_tag_content(llm_output, "confidence_score")
    
    return response if float(confidence) >= 0.7 else f"The status of claim {claim_number} is: {status}"


def lambda_handler(event, context):
    logger.info(f"# New Lex event: {event}")

    # User input (may not be present in initial request)
    input_transcript = event.get("inputTranscript", "")

    # Bot information
    bot_id = event["bot"]["id"]
    bot_version = event["bot"]["version"]
    locale_id = event["bot"]["localeId"]

    # Proposed next state
    proposed_next_state = event.get("proposedNextState", None)

    # Session state
    session_state = event["sessionState"]
    intent = session_state["intent"]
    slots = session_state["intent"]["slots"]
    session_attributes = session_state["sessionAttributes"]
    invocation_source = event.get("invocationSource")

    # If Lex could not determine user's intent, use LLM to identify the intent
    if intent["name"] == "FallbackIntent":
        intents = get_intents(
            bot_id,
            bot_version,
            locale_id,
        )

        with open("intent_identification_prompt.txt", "r") as file:
            intent_identification_prompt = file.read()
        llm_output = invoke_bedrock(
            intent_identification_prompt.format(
                intents=intents, utterance=input_transcript
            ),
            MODEL_ID,
        )

        llm_identified_intent = extract_tag_content(llm_output, "intent_output")
        llm_confidence = extract_tag_content(llm_output, "confidence_score")

        if llm_identified_intent.upper() != "NOT SURE" and float(llm_confidence) >= 0.7:
            slots = get_slots(
                bot_id,
                bot_version,
                locale_id,
                llm_identified_intent,
            )
            next_slot = get_next_unfilled_slot(
                bot_id=bot_id,
                bot_version=bot_version,
                locale_id=locale_id,
                intent_name=llm_identified_intent,
                slots=slots
            )

            response = {
                "sessionState": {
                    "dialogAction": {
                        "type": "ElicitSlot",
                        "slotToElicit": next_slot,
                    },
                    "intent": {
                        "name": llm_identified_intent,
                        "slots": slots,
                        "state": "InProgress",
                    },
                    "sessionAttributes": session_attributes,
                }
            }
            if event.get("responseContentType", "").startswith("audio/"):
                response["messages"] = [{
                    "contentType": "PlainText",
                    "content": "What type of damage occurred to your home?"
                }]
            return response
        else:
            response = {
                "sessionState": {
                    "dialogAction": {"type": "ElicitIntent"},
                    "intent": {"name": "FallbackIntent", "state": "Failed"},
                    "sessionAttributes": session_attributes,
                }
            }
            # Always include messages for FallbackIntent to ensure proper voice response
            response["messages"] = [{
                "contentType": "PlainText",
                "content": "I'm sorry, I didn't understand that. Could you please rephrase your request?"
            }]
            return response

    # Handle claim status check
    elif intent["name"] == "CheckClaimStatus":
        claim_number = slots.get("ClaimNumber", {}).get("value", {}).get("originalValue")
        
        if validate_claim_number(claim_number):
            status = get_claim_status(claim_number)
            response = generate_status_response(claim_number, status)
            
            lex_response = {
                "sessionState": {
                    "dialogAction": {"type": "Close"},
                    "intent": {
                        "name": "CheckClaimStatus",
                        "state": "Fulfilled"
                    }
                },
                "messages": [{
                    "contentType": "PlainText",
                    "content": response
                }]
            }
            return lex_response
        else:
            lex_response = {
                "sessionState": {
                    "dialogAction": {
                        "type": "ElicitSlot",
                        "slotToElicit": "ClaimNumber"
                    },
                    "intent": {
                        "name": "CheckClaimStatus",
                        "slots": slots,
                        "state": "InProgress"
                    }
                },
                "messages": [{
                    "contentType": "PlainText",
                    "content": "Please provide a valid claim number in the format CLM-XXXXXX"
                }]
            }
            return lex_response
            
    # If user is elicited for slot, use LLM to assist mapping the utterance to slot type values
    elif invocation_source == "DialogCodeHook":

    # Get all slots for this intent to determine the first slot
        all_slots = get_slots(
            bot_id=bot_id,
            bot_version=bot_version,
            locale_id=locale_id,
            intent=intent["name"]
        )
        first_slot = next(iter(all_slots)) if all_slots else None

        # Check if this is the initial intent recognition
        is_initial_recognition = (
            proposed_next_state and
            proposed_next_state.get("prompt", {}).get("attempt") == "Initial" and
            not any(slot is not None for slot in slots.values()) and
            proposed_next_state.get("dialogAction", {}).get("slotToElicit") == first_slot
        )

        if is_initial_recognition:
            # Delegate to Lex for the initial intent recognition
            response = {
                "sessionState": {
                    "dialogAction": {"type": "Delegate"},
                    "intent": {
                        "name": intent["name"],
                        "slots": slots,
                        "state": "InProgress"
                    },
                    "sessionAttributes": session_attributes,
                }
            }
            if event.get("responseContentType", "").startswith("audio/"):
                response["messages"] = [{
                    "contentType": "PlainText",
                    "content": "What type of damage occurred to your home?"
                }]
            return response
        
        
        
        transcriptions = event.get("transcriptions", [])
        is_slot_miss = False
    
        if transcriptions:
            resolved_context = transcriptions[0].get("resolvedContext", {})
            if (resolved_context.get("intent") == "FallbackIntent" or 
                (proposed_next_state and proposed_next_state.get("dialogAction", {}).get("type") == "ElicitSlot")):
                is_slot_miss = True
            
        if is_slot_miss and proposed_next_state:
            # Get the current slot being elicited from proposedNextState
            current_slot = proposed_next_state.get("dialogAction", {}).get("slotToElicit")
            
            # Get slot type information to check if it's a custom slot
            slot_values = get_slot_values(
                bot_id=bot_id,
                bot_version=bot_version,
                locale_id=locale_id,
                intent=intent["name"],
                slot_type=current_slot
            )

            if slot_values:
                with open("slot_assistance_prompt.txt", "r") as file:
                    slot_assistance_prompt = file.read()
                llm_output = invoke_bedrock(
                    slot_assistance_prompt.format(
                        slot_values=slot_values, utterance=input_transcript
                    ),
                    MODEL_ID,
                )
                llm_mapped_slot = extract_tag_content(llm_output, "slot_output")
                llm_confidence = extract_tag_content(llm_output, "confidence_score")

                if llm_mapped_slot.upper() != "NOT SURE" and float(llm_confidence) >= 0.7:
                    slots = set_slot(
                        slots,
                        current_slot,
                        input_transcript,
                        llm_mapped_slot,
                    )

                    next_slot = get_next_unfilled_slot(
                        bot_id=bot_id,
                        bot_version=bot_version,
                        locale_id=locale_id,
                        intent_name=intent["name"],
                        slots=slots
                    )

                    if next_slot:
                        response = {
                            "sessionState": {
                                "dialogAction": {
                                    "type": "ElicitSlot",
                                    "slotToElicit": next_slot,
                                },
                                "intent": {
                                    "name": intent["name"],
                                    "slots": slots,
                                    "state": "InProgress",
                                },
                                "sessionAttributes": session_attributes,
                            }
                        }
                        if event.get("responseContentType", "").startswith("audio/"):
                            if next_slot == "PersonalInjury":
                                response["messages"] = [{
                                    "contentType": "PlainText",
                                    "content": "Were there any injuries during the incident?"
                                }]
                            elif next_slot == "Damage":
                                response["messages"] = [{
                                    "contentType": "PlainText",
                                    "content": "What type of damage occurred to your home?"
                                }]
                        return response
                    else:
                        response = {
                            "sessionState": {
                                "dialogAction": {"type": "Delegate"},
                                "intent": {
                                    "name": intent["name"],
                                    "slots": slots,
                                    "state": "ReadyForFulfillment",
                                },
                                "sessionAttributes": session_attributes,
                            }
                        }
                        if event.get("responseContentType", "").startswith("audio/"):
                            response["messages"] = [{
                                "contentType": "PlainText",
                                "content": "Thank you for providing those details. I'll help process your claim."
                            }]
                        return response

                else:
                    response = {
                        "sessionState": {
                            "dialogAction": {
                                "type": "ElicitSlot",
                                "slotToElicit": current_slot,
                            },
                            "intent": {
                                "name": intent["name"],
                                "slots": slots,
                                "state": "InProgress",
                            },
                            "sessionAttributes": session_attributes,
                        }
                    }
                    if event.get("responseContentType", "").startswith("audio/"):
                        if current_slot == "PersonalInjury":
                            response["messages"] = [{
                                "contentType": "PlainText",
                                "content": "I didn't catch that. Could you please describe any injuries that occurred?"
                            }]
                        elif current_slot == "Damage":
                            response["messages"] = [{
                                "contentType": "PlainText",
                                "content": "I didn't catch that. What type of damage occurred to your home?"
                            }]
                    return response

    # For all other cases, delegate to Lex
    response = {
        "sessionState": {
            "dialogAction": {"type": "Delegate"},
            "intent": {"name": intent["name"], "slots": slots, "state": "InProgress"},
            "sessionAttributes": session_attributes,
        }
    }

    # Add messages if we need to provide a response
    if event.get("responseContentType", "").startswith("audio/"):
        response["messages"] = [{
            "contentType": "PlainText",
            "content": "How can I help you with your claim today?"
        }]

    return response
