
import logging
import google.generativeai as genai

from config import GEMINI_API_KEY
import json


genai.configure(api_key=GEMINI_API_KEY)


SYSTEM_PROMPT = """
        You are a context-aware content moderator for a sensitive mental health chat application. 
        Your goal is to ensure user safety by evaluating a new message within the context of the recent conversation history.

        Your moderation categories are:
        - SELF_HARM_ALERT: A clear statement of intent to self-harm, suicide, or severe depression.
        - BLOCK: A violation like hate speech, bullying, threats, or severe toxicity.
        - SAFE: The message is not a violation, especially when considering the context (e.g., talk about "dying" in a video game is safe).

        You MUST respond with ONLY a JSON object in the following format, with no other text or explanation:
        {"decision": "CATEGORY", "reason": "A brief, one-sentence explanation for your decision."}

        Example 1 (Harmful):
        History:
        userA: You are useless.
        New Message:
        userB: I am going to end it all.
        Your response:
        {"decision": "SELF_HARM_ALERT", "reason": "The user is expressing suicidal ideation in response to being bullied."}

        Example 2 (Safe with Context):
        History:
        userA: That boss fight was impossible, I must have died 20 times.
        New Message:
        userB: Me too, I just want to die.
        Your response:
        {"decision": "SAFE", "reason": "The user is expressing frustration about a video game, not actual self-harm intent."}
    """

async def analyze_with_gemini_context(conversation_history, new_message_text, user_email):
    """
    Analyzes a new message using Gemini, providing the last few messages as context.
    Returns the final decision ('SAFE', 'BLOCK', 'SELF_HARM_ALERT').
    """
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')

        
        formatted_history = "\n".join([f"{msg['email']}: {msg['text']}" for msg in conversation_history])
        
        full_prompt = f"""
        {SYSTEM_PROMPT}

        Here is the data for your evaluation:
        
        **Conversation History:**
        {formatted_history}

        **New Message to Evaluate:**
        {user_email}: {new_message_text}
        """

        
        response = await model.generate_content_async(full_prompt)
        
        
        cleaned_response = response.text.strip().replace("```json", "").replace("```", "")
        result_json = json.loads(cleaned_response)

        decision = result_json.get("decision", "SAFE").upper()
        reason = result_json.get("reason", "No reason provided by AI.")
        
        logging.info(f"Gemini contextual check. Decision: {decision}, Reason: {reason}")
        return decision, reason

    except Exception as e:
        logging.error(f"Error during Gemini contextual analysis: {e}")
        return None, None