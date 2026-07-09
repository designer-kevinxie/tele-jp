from google import genai
from dotenv import load_dotenv
import os

# 读取 .env
load_dotenv()

# 创建 client
client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

# 生成内容
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="请用简单日语介绍大阪。"
)

# 输出
print(response.text)