import json
import urllib.request

url = 'https://api.llama.fi/overview/fees?dataType=dailyRevenue'
try:
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
        print('HTTP:', response.status)
        print('Keys:', list(data)[:20])
        print('Protocols:', len(data.get('protocols', [])))
except Exception as exc:
    print(type(exc).__name__ + ':', exc)
    raise
