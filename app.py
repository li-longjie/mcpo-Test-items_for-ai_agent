from flask import Flask, request, jsonify, render_template, session
import requests
import os
import logging
import traceback
import re
import uuid
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-here")

# 硅基流动API配置
GUIJI_API_KEY = ""  # 替换为您的真实API密钥
GUIJI_API_URL = "https://api.siliconflow.cn/v1/chat/completions"

# MCP Fetch服务配置
MCP_FETCH_URL = "http://127.0.0.1:8000/fetch/fetch"

# MCP Time服务配置
MCP_TIME_URL = "http://127.0.0.1:8000/time/time"

# 以下配置已不再需要，保留为注释以备参考
# 或方案2：使用根路径
# MCP_TIME_URL = "http://127.0.0.1:8000/"

# 或方案3：尝试其他可能的端点
# MCP_TIME_URL = "http://127.0.0.1:8000/api/time"

@app.route("/")
def index():
    # 如果没有会话ID，创建一个
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        session['messages'] = []
    
    return render_template("chat.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message", "").strip()
    
    if not user_message:
        return jsonify({"error": "消息不能为空"}), 400
    
    try:
        # 保存用户消息
        messages = session.get('messages', [])
        messages.append({"role": "user", "content": user_message, "timestamp": datetime.now().isoformat()})
        
        # 初始化响应内容
        response_text = ""
        
        # 检查是否是时间相关查询
        time_keywords = ["时间", "几点", "日期", "today", "time", "date", "clock", "现在"]
        is_time_query = any(keyword in user_message.lower() for keyword in time_keywords)
        
        # 检查消息中是否包含URL
        urls = extract_urls(user_message)
        
        if is_time_query and not urls:
            # 时间相关查询
            logger.info("检测到时间相关查询")
            time_info = fetch_time()
            
            if "error" in time_info:
                response_text = f"获取时间信息失败: {time_info['error']}"
            else:
                # 将时间信息传递给大模型处理
                time_prompt = ""
                
                # 格式化时间信息
                if isinstance(time_info, list) and len(time_info) > 0:
                    time_data = time_info[0]  # 获取第一个时间数据
                    timezone = time_data.get('timezone', 'Unknown')
                    datetime_str = time_data.get('datetime', 'Unknown')
                    is_dst = time_data.get('is_dst', False)
                    
                    # 提取更易读的时间部分
                    try:
                        dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
                        readable_time = dt.strftime('%Y年%m月%d日 %H:%M:%S')
                        time_prompt = f"用户询问当前时间。当前时间信息如下:\n时区: {timezone}\n日期时间: {readable_time}\n是否夏令时: {'是' if is_dst else '否'}\n\n请根据这些信息，以自然、友好的方式告诉用户现在的时间。如果用户问的是特定问题，请针对性回答。"
                    except:
                        time_prompt = f"用户询问当前时间。当前时间信息如下:\n时区: {timezone}\n日期时间: {datetime_str}\n是否夏令时: {'是' if is_dst else '否'}\n\n请根据这些信息，以自然、友好的方式告诉用户现在的时间。如果用户问的是特定问题，请针对性回答。"
                else:
                    time_prompt = f"用户询问当前时间。当前时间信息如下:\n{time_info}\n\n请根据这些信息，以自然、友好的方式告诉用户现在的时间。如果用户问的是特定问题，请针对性回答。"
                
                # 添加用户的原始问题，以便模型能够针对性回答
                time_prompt += f"\n\n用户的原始问题是: \"{user_message}\""
                
                # 调用大模型处理时间信息
                logger.info(f"发送时间提示词到大模型: {time_prompt}")
                response_text = query_guiji_model(time_prompt)
        elif urls:
            # 有URL，需要处理
            logger.info(f"检测到URL: {urls}")
            
            # 获取第一个URL的内容
            url = urls[0]
            web_content = fetch_webpage(url)
            
            # 检查是否成功获取网页内容
            if isinstance(web_content, dict) and "error" in web_content:
                error_msg = f"获取网页内容失败: {web_content['error']}"
                logger.error(error_msg)
                response_text = f"我尝试访问您提供的网址({url})，但遇到了问题: {web_content['error']}"
            else:
                # 提取内容
                content_text = ""
                if isinstance(web_content, str):
                    content_text = web_content
                elif isinstance(web_content, dict):
                    if "content" in web_content:
                        content_text = web_content["content"]
                    elif "text" in web_content:
                        content_text = web_content["text"]
                    else:
                        content_text = str(web_content)
                else:
                    content_text = str(web_content)
                
                # 限制内容长度，避免提示词过长
                if len(content_text) > 8000:
                    content_text = content_text[:8000] + "...(内容已截断)"
                
                logger.info(f"成功获取网页内容，长度: {len(content_text)}")
                
                # 构建提示词
                prompt = f"以下是从URL '{url}' 获取的网页内容。请分析并回答用户的问题。\n\n{content_text}\n\n用户的问题是: {user_message}"
                
                # 调用模型获取回答
                response_text = query_guiji_model(prompt)
        else:
            # 没有URL，直接进行常规对话
            response_text = query_guiji_model(user_message)
        
        # 保存AI回复
        messages.append({"role": "assistant", "content": response_text, "timestamp": datetime.now().isoformat()})
        session['messages'] = messages
        
        return jsonify({
            "response": response_text,
            "messages": messages[-10:]  # 返回最近10条消息用于显示
        })
    
    except Exception as e:
        error_msg = f"处理请求时出错: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        return jsonify({"error": error_msg}), 500

@app.route("/api/history", methods=["GET"])
def get_history():
    messages = session.get('messages', [])
    return jsonify({"messages": messages})

@app.route("/api/clear", methods=["POST"])
def clear_history():
    session['messages'] = []
    return jsonify({"status": "success"})

@app.route("/api/time", methods=["GET"])
def get_time():
    time_info = fetch_time()
    return jsonify(time_info)

def extract_urls(text):
    """从文本中提取所有URL"""
    # 修改正则表达式，匹配URL直到空格或行尾
    url_pattern = re.compile(r'(https?://[^\s]+)')
    urls = url_pattern.findall(text)
    
    # 清理URL，移除可能附加的非URL字符
    cleaned_urls = []
    for url in urls:
        # 如果URL后面有中文或其他非URL字符，进行分割
        # 常见的URL字符包括字母、数字、点、斜杠、连字符、下划线等
        valid_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/?#[]@!$&\'()*+,;=')
        end_pos = len(url)
        
        for i, char in enumerate(url):
            if i > 0 and char not in valid_chars:
                end_pos = i
                break
        
        cleaned_url = url[:end_pos]
        cleaned_urls.append(cleaned_url)
    
    return cleaned_urls

def fetch_webpage(url, max_length=10000, start_index=0, raw=False):
    """使用MCP Fetch获取网页内容"""
    try:
        logger.info(f"发送请求到MCP Fetch: {url}")
        
        # 确保URL格式正确
        if not url.startswith('http'):
            url = 'https://' + url
        
        # 准备请求体
        request_body = {
            "url": url,
            "max_length": max_length,
            "start_index": start_index,
            "raw": raw
        }
        
        logger.info(f"请求体: {request_body}")
        
        # 使用正确的端点
        fetch_url = "http://127.0.0.1:8000/fetch/fetch"
        logger.info(f"请求URL: {fetch_url}")
        
        response = requests.post(
            fetch_url,
            json=request_body,
            timeout=30
        )
        
        logger.info(f"MCP Fetch响应状态码: {response.status_code}")
        
        # 处理响应
        if response.status_code != 200:
            error_text = response.text
            logger.error(f"MCP Fetch错误响应: {error_text}")
            return {"error": f"MCP服务返回HTTP错误: {response.status_code} - {error_text[:100]}"}
        
        # 获取响应内容
        try:
            result = response.json()
            logger.info(f"MCP Fetch返回类型: {type(result)}")
            return result
        except ValueError:
            # 如果不是JSON，返回文本内容
            text_content = response.text
            logger.info(f"MCP返回了非JSON内容，长度: {len(text_content)}")
            return text_content
    
    except Exception as e:
        logger.error(f"获取网页内容异常: {str(e)}")
        logger.error(traceback.format_exc())
        return {"error": str(e)}

def query_guiji_model(prompt):
    """调用硅基流动API获取回答"""
    try:
        logger.info(f"提示词长度: {len(prompt)}")
        
        # 准备API请求
        payload = {
            "model": "deepseek-ai/DeepSeek-V3",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "max_tokens": 1000,
            "temperature": 0.7,
            "top_p": 0.7,
            "top_k": 50,
            "frequency_penalty": 0.5,
            "n": 1
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GUIJI_API_KEY}"
        }
        
        # 调用硅基流动API
        logger.info("发送请求到硅基流动API")
        response = requests.post(
            GUIJI_API_URL,
            headers=headers,
            json=payload,
            timeout=90
        )
        
        logger.info(f"硅基流动API响应状态码: {response.status_code}")
        
        # 检查响应
        if response.status_code != 200:
            error_text = response.text
            try:
                error_response = response.json()
                error_message = error_response.get("message", "未知错误")
                return f"抱歉，API调用失败: {error_message}"
            except:
                return f"抱歉，API调用失败: {error_text[:100]}"
        
        result = response.json()
        
        # 提取回答
        if "choices" in result and len(result["choices"]) > 0:
            return result["choices"][0]["message"]["content"]
        else:
            return "抱歉，无法获取有效回答。"
    
    except Exception as e:
        logger.error(f"调用模型异常: {str(e)}")
        logger.error(traceback.format_exc())
        return f"抱歉，调用模型时出错: {str(e)}"

def fetch_time():
    """使用MCP Time获取时间信息"""
    try:
        logger.info("发送请求到MCP Time服务")
        
        # 准备请求体 - 包含timezone参数
        request_body = {
            "timezone": "America/New_York"  # 使用配置文件中指定的时区
        }
        
        # 尝试不同的端点格式
        time_endpoints = [
            # 标准端点
            "http://127.0.0.1:8000/time/time",
            # 根路径
            "http://127.0.0.1:8000/time/",
            # 直接访问get_current_time
            "http://127.0.0.1:8000/time/get_current_time"
        ]
        
        for endpoint in time_endpoints:
            try:
                logger.info(f"尝试时间端点: {endpoint}")
                response = requests.post(
                    endpoint,
                    json=request_body,
                    timeout=10
                )
                
                logger.info(f"MCP Time响应状态码: {response.status_code}")
                
                if response.status_code == 200:
                    # 成功获取响应
                    try:
                        result = response.json()
                        logger.info(f"MCP Time返回: {result}")
                        return result
                    except ValueError:
                        text_content = response.text
                        logger.info(f"MCP Time返回了非JSON内容: {text_content}")
                        return {"time": text_content}
            except Exception as e:
                logger.warning(f"尝试端点 {endpoint} 失败: {str(e)}")
                continue
        
        # 如果所有端点都失败，返回错误
        return {"error": "无法连接到MCP Time服务，所有端点都返回失败"}
    
    except Exception as e:
        logger.error(f"获取时间信息异常: {str(e)}")
        logger.error(traceback.format_exc())
        return {"error": str(e)}

if __name__ == "__main__":
    logger.info("启动Web应用...")
    app.run(debug=True, port=5000)