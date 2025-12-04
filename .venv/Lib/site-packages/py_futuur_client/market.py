class MarketAPI:
    def __init__(self, client):
        self.client = client
    
    def list(self, params: dict={}):
        """
        Fetches the list of markets
        """
        if not params:
            params = {'ordering': '-created_on'}
        return self.client._make_request(method='GET', endpoint='markets', params=params)
    
    def get(self, id):
        """
        Fetch details for a specific market
        """
        return self.client._make_request(method='Get', endpoint=f'markets/{id}')
    
    def get_order_book(self, id, params: dict={}):
        """
        Retrieve the aggregated order book for a given question, grouped by price levels.

        This endpoint returns the total number of shares available at each price point for both bid and ask orders.
        """
        if not params:
            params = {'currency_mode': 'play_money'}
        return self.client._make_request(endpoint=f'markets/{id}/order_book/', params=params)

    def get_related_markets(self, id):
        """
        Return related markets of a market
        """
        return self.client._make_request(endpoint=f'markets/{id}/related_markets/')
    
    def suggest_market(self, payload: dict):
        """
        Suggest a market
        """
        return self.client._make_request(method='POST', endpoint='markets/suggest_market/', payload=payload)