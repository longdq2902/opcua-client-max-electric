import requests

# URL API
url = "http://localhost:5001/api/v1/datapoint-value"

# Dữ liệu JSON cần gửi
data = {
    "ioa": 208,
    "value": 10021
}

# Header với Content-Type
headers = {
    "Content-Type": "application/json"
}

# Gửi yêu cầu PUT
response = requests.put(url, headers=headers, json=data)

# In kết quả phản hồi
print("Status code:", response.status_code)
print("Response body:", response.text)
