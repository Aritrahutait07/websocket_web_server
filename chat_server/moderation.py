import logging
import json
from config import AZURE_CONTENT_SAFETY_KEY, AZURE_CONTENT_SAFETY_ENDPOINT


API_VERSION = "2023-10-01"

THRESHOLDS = {
    'Hate': 1,
    'Violence':1,
    'Sexual': 1,
    'SelfHarm': 1, 
}

async def analyze_text(session, text_to_analyze: str):
    
    if not AZURE_CONTENT_SAFETY_KEY or not AZURE_CONTENT_SAFETY_ENDPOINT:
        logging.error("Azure Content Safety credentials are not configured.")
        return None

    
    endpoint_url = f"{AZURE_CONTENT_SAFETY_ENDPOINT}contentsafety/text:analyze?api-version={API_VERSION}"

    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_CONTENT_SAFETY_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "text": text_to_analyze,
        
        "categories": ["Hate", "Violence", "SelfHarm", "Sexual"]
    }

    try:
        
        async with session.post(endpoint_url, headers=headers, json=payload) as response:
            
            if response.status == 200:
                return await response.json()
            else:
                
                error_text = await response.text()
                logging.error(f"Azure API call failed with status {response.status}: {error_text}")
                return None
    except Exception as e:
        logging.error(f"An exception occurred while calling Azure API: {e}")
        return None

def evaluate_analysis(api_response: dict):
    
    if not api_response or 'categoriesAnalysis' not in api_response:
        
        return 'OK', None

    for category_result in api_response['categoriesAnalysis']:
        category_name = category_result['category']
        severity = category_result.get('severity', 0)

        
        if severity >= THRESHOLDS.get(category_name, 999):
            
            if category_name == 'SelfHarm':
                return 'SELF_HARM_ALERT', category_name
            else:
                return 'BLOCK', category_name

    
    return 'OK', None