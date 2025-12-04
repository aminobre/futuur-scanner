from collections import OrderedDict
from urllib.parse import urlencode
import hmac
import hashlib
from datetime import datetime
from requests import request
from .market import MarketAPI

class Client:
    def __init__(self, public_key: str, private_key: str):
        self.public_key = public_key
        self.private_key = private_key
        self.base_url = 'https://api.futuur.com/api/v1/'
        self.market = MarketAPI(client=self)
    
    def _generate_signature(self, params: dict) -> tuple:
        """
        Generates HMAC-SHA512 signature as per Futuur documentation.
        Includes Key and Timestamp in the signing string.
        """

        # 1. Create a copy of params with Key and Timestamp
        timestamp = str(int(datetime.utcnow().timestamp()))
        _params = params.copy()
        _params['Key'] = self.public_key
        _params['Timestamp'] = timestamp
        
        # 2. Order params alphabetically
        sorted_params = OrderedDict(sorted(_params.items()))
        
        # 3. Convert ordered params to query string
        query_string = urlencode(sorted_params)
        
        # 4. Generate HMAC-SHA512
        signature = hmac.new(
            self.private_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        
        return signature, timestamp
    
    def _make_request(self, endpoint: str, method: str='GET', params: dict = None, payload: dict = None):
        """
        Makes a request to the API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            params: Query parameters (for GET)
            payload: JSON body (for POST)
        """
        if params is None:
            params = {}
        
        # Build URL
        url = self.base_url + endpoint
        
        # For GET, add params to query string
        if method.upper() == 'GET' and params:
            query_str = urlencode(params)
            url = f"{url}?{query_str}"
        
        # Generate headers
        signature, timestamp = self._generate_signature(params if method.upper() == 'GET' else (payload or {}))
        
        headers = {
            'Key': self.public_key,
            'Timestamp': timestamp,
            'HMAC': signature,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        
        # Make request
        request_args = {
            'method': method,
            'url': url,
            'headers': headers,
        }
        
        if method.upper() in ['POST', 'PUT', 'PATCH'] and payload:
            request_args['json'] = payload
        
        response = request(**request_args)
        
        try:
            return response.json()
        except:
            return {'error': 'Failed to parse response', 'text': response.text}
