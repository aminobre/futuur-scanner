from futuur_client import _client

client = _client()
markets = client.market.list()

print("Type:", type(markets))
print("Keys:", getattr(markets, "keys", lambda: [])())

print("Raw response:")
print(markets)
