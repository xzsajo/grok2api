import os
import json
import uuid
import time
import base64
import sys
import inspect
from loguru import logger

import requests
from flask import Flask, request, Response, jsonify, stream_with_context
from curl_cffi import requests as curl_requests
from werkzeug.middleware.proxy_fix import ProxyFix


class Logger:
    def __init__(self, level="INFO", colorize=True, format=None):
        logger.remove()

        if format is None:
            format = (
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{extra[filename]}</cyan>:<cyan>{extra[function]}</cyan>:<cyan>{extra[lineno]}</cyan> | "
                "<level>{message}</level>"
            )

        logger.add(
            sys.stderr, 
            level=level, 
            format=format,
            colorize=colorize,
            backtrace=True,
            diagnose=True
        )

        self.logger = logger

    def _get_caller_info(self):
        frame = inspect.currentframe()
        try:
            caller_frame = frame.f_back.f_back
            full_path = caller_frame.f_code.co_filename
            function = caller_frame.f_code.co_name
            lineno = caller_frame.f_lineno
            
            filename = os.path.basename(full_path)
            
            return {
                'filename': filename,
                'function': function,
                'lineno': lineno
            }
        finally:
            del frame 

    def info(self, message, source="API"):
        caller_info = self._get_caller_info()
        self.logger.bind(**caller_info).info(f"[{source}] {message}")
        
    def error(self, message, source="API"):
        caller_info = self._get_caller_info()
        
        if isinstance(message, Exception):
            self.logger.bind(**caller_info).exception(f"[{source}] {str(message)}")
        else:
            self.logger.bind(**caller_info).error(f"[{source}] {message}")
    
    def warning(self, message, source="API"):
        caller_info = self._get_caller_info()
        self.logger.bind(**caller_info).warning(f"[{source}] {message}")
    
    def debug(self, message, source="API"):
        caller_info = self._get_caller_info()
        self.logger.bind(**caller_info).debug(f"[{source}] {message}")

    async def request_logger(self, request):
        caller_info = self._get_caller_info()
        self.logger.bind(**caller_info).info(f"请求: {request.method} {request.path}", "Request")

logger = Logger(level="INFO")


CONFIG = {
    "MODELS": {
        'grok-2': 'grok-latest',
        'grok-2-imageGen': 'grok-latest',
        'grok-2-search': 'grok-latest',
        "grok-3": "grok-3",
        "grok-3-search": "grok-3",
        "grok-3-imageGen": "grok-3",
        "grok-3-deepsearch": "grok-3",
        "grok-3-reasoning": "grok-3"
    },
    "API": {
        "IS_CUSTOM_SSO": os.environ.get("IS_CUSTOM_SSO", "false").lower() == "true",
        "BASE_URL": "https://grok.com",
        "API_KEY": os.environ.get("API_KEY", "sk-123456"),
        "SIGNATURE_COOKIE": None,
        "PICGO_KEY": os.environ.get("PICGO_KEY") or None,
        "TUMY_KEY": os.environ.get("TUMY_KEY") or None,
        "RETRY_TIME": 1000,
        "PROXY": os.environ.get("PROXY") or None
    },
    "SERVER": {
        "PORT": int(os.environ.get("PORT", 5200))
    },
    "RETRY": {
        "MAX_ATTEMPTS": 2
    },
    "SHOW_THINKING": os.environ.get("SHOW_THINKING") == "true",
    "IS_THINKING": False,
    "IS_IMG_GEN": False,
    "IS_IMG_GEN2": False,
    "ISSHOW_SEARCH_RESULTS": os.environ.get("ISSHOW_SEARCH_RESULTS", "true").lower() == "true"
}


DEFAULT_HEADERS = {
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Content-Type': 'text/plain;charset=UTF-8',
    'Connection': 'keep-alive',
    'Origin': 'https://grok.com',
    'Priority': 'u=1, i',
    'Sec-Ch-Ua': '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Baggage': 'sentry-public_key=b311e0f2690c81f25e2c4cf6d4f7ce1c'
}

class AuthTokenManager:
    def __init__(self):
        self.token_model_map = {}
        self.expired_tokens = set()
        self.token_status_map = {}

        self.model_config = {
            "grok-2": {
                "RequestFrequency": 30,
                "ExpirationTime": 1 * 60 * 60 * 1000  # 1小时
            },
            "grok-3": {
                "RequestFrequency": 20,
                "ExpirationTime": 2 * 60 * 60 * 1000  # 2小时
            },
            "grok-3-deepsearch": {
                "RequestFrequency": 10,
                "ExpirationTime": 24 * 60 * 60 * 1000  # 24小时
            },
            "grok-3-reasoning": {
                "RequestFrequency": 10,
                "ExpirationTime": 24 * 60 * 60 * 1000  # 24小时
            }
        }
        self.token_reset_switch = False
        self.token_reset_timer = None

    def add_token(self, token):
        sso = token.split("sso=")[1].split(";")[0]
        for model in self.model_config.keys():
            if model not in self.token_model_map:
                self.token_model_map[model] = []
            if sso not in self.token_status_map:
                self.token_status_map[sso] = {}
            
            existing_token_entry = next((entry for entry in self.token_model_map[model] if entry["token"] == token), None)
            
            if not existing_token_entry:
                self.token_model_map[model].append({
                    "token": token,
                    "RequestCount": 0,
                    "AddedTime": int(time.time() * 1000),
                    "StartCallTime": None
                })
                
                if model not in self.token_status_map[sso]:
                    self.token_status_map[sso][model] = {
                        "isValid": True,
                        "invalidatedTime": None,
                        "totalRequestCount": 0
                    }
    
    def set_token(self, token):
        models = list(self.model_config.keys())
        self.token_model_map = {model: [{
            "token": token,
            "RequestCount": 0,
            "AddedTime": int(time.time() * 1000),
            "StartCallTime": None
        }] for model in models}
        
        sso = token.split("sso=")[1].split(";")[0]
        self.token_status_map[sso] = {model: {
            "isValid": True,
            "invalidatedTime": None,
            "totalRequestCount": 0
        } for model in models}
    
    def delete_token(self, token):
        try:
            sso = token.split("sso=")[1].split(";")[0]
            for model in self.token_model_map:
                self.token_model_map[model] = [entry for entry in self.token_model_map[model] if entry["token"] != token]
            
            if sso in self.token_status_map:
                del self.token_status_map[sso]
                
            logger.info(f"令牌已成功移除: {token}", "TokenManager")
            return True
        except Exception as error:
            logger.error(f"令牌删除失败: {str(error)}")
            return False
    
    def get_next_token_for_model(self, model_id):
        normalized_model = self.normalize_model_name(model_id)
        
        if normalized_model not in self.token_model_map or not self.token_model_map[normalized_model]:
            return None
        
        token_entry = self.token_model_map[normalized_model][0]
        
        if token_entry:
            if token_entry["StartCallTime"] is None:
                token_entry["StartCallTime"] = int(time.time() * 1000)
            
            if not self.token_reset_switch:
                self.start_token_reset_process()
                self.token_reset_switch = True
            
            token_entry["RequestCount"] += 1
            
            if token_entry["RequestCount"] > self.model_config[normalized_model]["RequestFrequency"]:
                self.remove_token_from_model(normalized_model, token_entry["token"])
                next_token_entry = self.token_model_map[normalized_model][0] if self.token_model_map[normalized_model] else None
                return next_token_entry["token"] if next_token_entry else None
            
            sso = token_entry["token"].split("sso=")[1].split(";")[0]
            if sso in self.token_status_map and normalized_model in self.token_status_map[sso]:
                if token_entry["RequestCount"] == self.model_config[normalized_model]["RequestFrequency"]:
                    self.token_status_map[sso][normalized_model]["isValid"] = False
                    self.token_status_map[sso][normalized_model]["invalidatedTime"] = int(time.time() * 1000)
                self.token_status_map[sso][normalized_model]["totalRequestCount"] += 1
            
            return token_entry["token"]
        
        return None
    
    def remove_token_from_model(self, model_id, token):
        normalized_model = self.normalize_model_name(model_id)
        
        if normalized_model not in self.token_model_map:
            logger.error(f"模型 {normalized_model} 不存在", "TokenManager")
            return False
        
        model_tokens = self.token_model_map[normalized_model]
        token_index = next((i for i, entry in enumerate(model_tokens) if entry["token"] == token), -1)
        
        if token_index != -1:
            removed_token_entry = model_tokens.pop(token_index)
            self.expired_tokens.add((
                removed_token_entry["token"],
                normalized_model,
                int(time.time() * 1000)
            ))
            
            if not self.token_reset_switch:
                self.start_token_reset_process()
                self.token_reset_switch = True
            
            logger.info(f"模型{model_id}的令牌已失效，已成功移除令牌: {token}", "TokenManager")
            return True
        
        logger.error(f"在模型 {normalized_model} 中未找到 token: {token}", "TokenManager")
        return False
    
    def get_expired_tokens(self):
        return list(self.expired_tokens)
    
    def normalize_model_name(self, model):
        if model.startswith('grok-') and 'deepsearch' not in model and 'reasoning' not in model:
            return '-'.join(model.split('-')[:2])
        return model
    
    def get_token_count_for_model(self, model_id):
        normalized_model = self.normalize_model_name(model_id)
        return len(self.token_model_map.get(normalized_model, []))
    
    def get_remaining_token_request_capacity(self):
        remaining_capacity_map = {}
        
        for model in self.model_config.keys():
            model_tokens = self.token_model_map.get(model, [])
            model_request_frequency = self.model_config[model]["RequestFrequency"]
            
            total_used_requests = sum(token_entry.get("RequestCount", 0) for token_entry in model_tokens)
            
            remaining_capacity = (len(model_tokens) * model_request_frequency) - total_used_requests
            remaining_capacity_map[model] = max(0, remaining_capacity)
        
        return remaining_capacity_map
    
    def get_token_array_for_model(self, model_id):
        normalized_model = self.normalize_model_name(model_id)
        return self.token_model_map.get(normalized_model, [])
    
    def start_token_reset_process(self):
        def reset_expired_tokens():
            now = int(time.time() * 1000)
            
            tokens_to_remove = set()
            for token_info in self.expired_tokens:
                token, model, expired_time = token_info
                expiration_time = self.model_config[model]["ExpirationTime"]
                
                if now - expired_time >= expiration_time:
                    if not any(entry["token"] == token for entry in self.token_model_map.get(model, [])):
                        if model not in self.token_model_map:
                            self.token_model_map[model] = []
                        
                        self.token_model_map[model].append({
                            "token": token,
                            "RequestCount": 0,
                            "AddedTime": now,
                            "StartCallTime": None
                        })
                    
                    sso = token.split("sso=")[1].split(";")[0]
                    if sso in self.token_status_map and model in self.token_status_map[sso]:
                        self.token_status_map[sso][model]["isValid"] = True
                        self.token_status_map[sso][model]["invalidatedTime"] = None
                        self.token_status_map[sso][model]["totalRequestCount"] = 0
                    
                    tokens_to_remove.add(token_info)
            
            self.expired_tokens -= tokens_to_remove
            
            for model in self.model_config.keys():
                if model not in self.token_model_map:
                    continue
                
                for token_entry in self.token_model_map[model]:
                    if not token_entry.get("StartCallTime"):
                        continue
                    
                    expiration_time = self.model_config[model]["ExpirationTime"]
                    if now - token_entry["StartCallTime"] >= expiration_time:
                        sso = token_entry["token"].split("sso=")[1].split(";")[0]
                        if sso in self.token_status_map and model in self.token_status_map[sso]:
                            self.token_status_map[sso][model]["isValid"] = True
                            self.token_status_map[sso][model]["invalidatedTime"] = None
                            self.token_status_map[sso][model]["totalRequestCount"] = 0
                        
                        token_entry["RequestCount"] = 0
                        token_entry["StartCallTime"] = None
        
        import threading
        # 启动一个线程执行定时任务，每小时执行一次
        def run_timer():
            while True:
                reset_expired_tokens()
                time.sleep(3600)
        
        timer_thread = threading.Thread(target=run_timer)
        timer_thread.daemon = True
        timer_thread.start()
    
    def get_all_tokens(self):
        all_tokens = set()
        for model_tokens in self.token_model_map.values():
            for entry in model_tokens:
                all_tokens.add(entry["token"])
        return list(all_tokens)
    
    def get_token_status_map(self):
        return self.token_status_map
    
class Utils:
    @staticmethod
    def organize_search_results(search_results):
        if not search_results or 'results' not in search_results:
            return ''
        
        results = search_results['results']
        formatted_results = []
        
        for index, result in enumerate(results):
            title = result.get('title', '未知标题')
            url = result.get('url', '#')
            preview = result.get('preview', '无预览内容')
            
            formatted_result = f"\r\n<details><summary>资料[{index}]: {title}</summary>\r\n{preview}\r\n\n[Link]({url})\r\n</details>"
            formatted_results.append(formatted_result)
        
        return '\n\n'.join(formatted_results)
    
    @staticmethod
    def create_auth_headers(model):
        return token_manager.get_next_token_for_model(model)
    
    @staticmethod
    def get_proxy_options():
        proxy = CONFIG["API"]["PROXY"]
        proxy_options = {}
        
        if proxy:
            logger.info(f"使用代理: {proxy}", "Server")
            proxy_options["proxies"] = {"https": proxy, "http": proxy}
            
            if proxy.startswith("socks5://"):
                proxy_options["proxies"] = {"https": proxy, "http": proxy}
                proxy_options["proxy_type"] = "socks5"
        
        return proxy_options

class GrokApiClient:
    def __init__(self, model_id):
        if model_id not in CONFIG["MODELS"]:
            raise ValueError(f"不支持的模型: {model_id}")
        self.model_id = CONFIG["MODELS"][model_id]
    
    def process_message_content(self, content):
        if isinstance(content, str):
            return content
        return None
    
    def get_image_type(self, base64_string):
        mime_type = 'image/jpeg'
        if 'data:image' in base64_string:
            import re
            matches = re.search(r'data:([a-zA-Z0-9]+\/[a-zA-Z0-9-.+]+);base64,', base64_string)
            if matches:
                mime_type = matches.group(1)
        
        extension = mime_type.split('/')[1]
        file_name = f"image.{extension}"
        
        return {
            "mimeType": mime_type,
            "fileName": file_name
        }
    
    def upload_base64_image(self, base64_data, url):
        try:
            if 'data:image' in base64_data:
                image_buffer = base64_data.split(',')[1]
            else:
                image_buffer = base64_data
            
            image_info = self.get_image_type(base64_data)
            mime_type = image_info["mimeType"]
            file_name = image_info["fileName"]
            
            upload_data = {
                "rpc": "uploadFile",
                "req": {
                    "fileName": file_name,
                    "fileMimeType": mime_type,
                    "content": image_buffer
                }
            }
            
            logger.info("发送图片请求", "Server")
            
            proxy_options = Utils.get_proxy_options()
            response = curl_requests.post(
                url,
                headers={
                    **DEFAULT_HEADERS,
                    "Cookie": CONFIG["API"]["SIGNATURE_COOKIE"]
                },
                json=upload_data,
                impersonate="chrome120",
                **proxy_options
            )
            
            if response.status_code != 200:
                logger.error(f"上传图片失败,状态码:{response.status_code}", "Server")
                return ''
            
            result = response.json()
            logger.info(f"上传图片成功: {result}", "Server")
            return result.get("fileMetadataId", "")
        
        except Exception as error:
            logger.error(str(error), "Server")
            return ''
    
    def prepare_chat_request(self, request):
        if ((request["model"] == 'grok-2-imageGen' or request["model"] == 'grok-3-imageGen') and 
            not CONFIG["API"]["PICGO_KEY"] and not CONFIG["API"]["TUMY_KEY"] and 
            request.get("stream", False)):
            raise ValueError("该模型流式输出需要配置PICGO或者TUMY图床密钥!")
        
        todo_messages = request["messages"]
        if request["model"] in ['grok-2-imageGen', 'grok-3-imageGen', 'grok-3-deepsearch']:
            last_message = todo_messages[-1]
            if last_message["role"] != 'user':
                raise ValueError('此模型最后一条消息必须是用户消息!')
            todo_messages = [last_message]
        
        file_attachments = []
        messages = ''
        last_role = None
        last_content = ''
        search = request["model"] in ['grok-2-search', 'grok-3-search']
        
        # 移除<think>标签及其内容和base64图片
        def remove_think_tags(text):
            import re
            text = re.sub(r'<think>[\s\S]*?<\/think>', '', text).strip()
            text = re.sub(r'!\[image\]\(data:.*?base64,.*?\)', '[图片]', text)
            return text
        
        def process_content(content):
            if isinstance(content, list):
                text_content = ''
                for item in content:
                    if item["type"] == 'image_url':
                        text_content += ("[图片]" if not text_content else '\n[图片]')
                    elif item["type"] == 'text':
                        text_content += (remove_think_tags(item["text"]) if not text_content else '\n' + remove_think_tags(item["text"]))
                return text_content
            elif isinstance(content, dict) and content is not None:
                if content["type"] == 'image_url':
                    return "[图片]"
                elif content["type"] == 'text':
                    return remove_think_tags(content["text"])
            return remove_think_tags(self.process_message_content(content))
        
        for current in todo_messages:
            role = 'assistant' if current["role"] == 'assistant' else 'user'
            is_last_message = current == todo_messages[-1]
            
            if is_last_message and "content" in current:
                if isinstance(current["content"], list):
                    for item in current["content"]:
                        if item["type"] == 'image_url':
                            processed_image = self.upload_base64_image(
                                item["image_url"]["url"],
                                f"{CONFIG['API']['BASE_URL']}/api/rpc"
                            )
                            if processed_image:
                                file_attachments.append(processed_image)
                elif isinstance(current["content"], dict) and current["content"].get("type") == 'image_url':
                    processed_image = self.upload_base64_image(
                        current["content"]["image_url"]["url"],
                        f"{CONFIG['API']['BASE_URL']}/api/rpc"
                    )
                    if processed_image:
                        file_attachments.append(processed_image)
            

            text_content = process_content(current.get("content", ""))
            
            if text_content or (is_last_message and file_attachments):
                if role == last_role and text_content:
                    last_content += '\n' + text_content
                    messages = messages[:messages.rindex(f"{role.upper()}: ")] + f"{role.upper()}: {last_content}\n"
                else:
                    messages += f"{role.upper()}: {text_content or '[图片]'}\n"
                    last_content = text_content
                    last_role = role
        
        return {
            "temporary": CONFIG["API"].get("IS_TEMP_CONVERSATION", False),
            "modelName": self.model_id,
            "message": messages.strip(),
            "fileAttachments": file_attachments[:4],
            "imageAttachments": [],
            "disableSearch": False,
            "enableImageGeneration": True,
            "returnImageBytes": False,
            "returnRawGrokInXaiRequest": False,
            "enableImageStreaming": False,
            "imageGenerationCount": 1,
            "forceConcise": False,
            "toolOverrides": {
                "imageGen": request["model"] in ['grok-2-imageGen', 'grok-3-imageGen'],
                "webSearch": search,
                "xSearch": search,
                "xMediaSearch": search,
                "trendsSearch": search,
                "xPostAnalyze": search
            },
            "enableSideBySide": True,
            "isPreset": False,
            "sendFinalMetadata": True,
            "customInstructions": "",
            "deepsearchPreset": "default" if request["model"] == 'grok-3-deepsearch' else "",
            "isReasoning": request["model"] == 'grok-3-reasoning'
        }

class MessageProcessor:
    @staticmethod
    def create_chat_response(message, model, is_stream=False):
        base_response = {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "created": int(time.time()),
            "model": model
        }
        
        if is_stream:
            return {
                **base_response,
                "object": "chat.completion.chunk",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "content": message
                    }
                }]
            }
        
        return {
            **base_response,
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": message
                },
                "finish_reason": "stop"
            }],
            "usage": None
        }

def process_model_response(response, model):
    result = {"token": None, "imageUrl": None}
    
    if CONFIG["IS_IMG_GEN"]:
        if response.get("cachedImageGenerationResponse") and not CONFIG["IS_IMG_GEN2"]:
            result["imageUrl"] = response["cachedImageGenerationResponse"]["imageUrl"]
        return result
    
    if model == 'grok-2':
        result["token"] = response.get("token")
    elif model in ['grok-2-search', 'grok-3-search']:
        if response.get("webSearchResults") and CONFIG["ISSHOW_SEARCH_RESULTS"]:
            result["token"] = f"\r\n<think>{Utils.organize_search_results(response['webSearchResults'])}</think>\r\n"
        else:
            result["token"] = response.get("token")
    elif model == 'grok-3':
        result["token"] = response.get("token")
    elif model == 'grok-3-deepsearch':
        if response.get("messageStepId") and not CONFIG["SHOW_THINKING"]:
            return result
        if response.get("messageStepId") and not CONFIG["IS_THINKING"]:
            result["token"] = "<think>" + response.get("token", "")
            CONFIG["IS_THINKING"] = True
        elif not response.get("messageStepId") and CONFIG["IS_THINKING"] and response.get("messageTag") == "final":
            result["token"] = "</think>" + response.get("token", "")
            CONFIG["IS_THINKING"] = False
        elif (response.get("messageStepId") and CONFIG["IS_THINKING"] and response.get("messageTag") == "assistant") or response.get("messageTag") == "final":
            result["token"] = response.get("token")            
    elif model == 'grok-3-reasoning':
        if response.get("isThinking") and not CONFIG["SHOW_THINKING"]:
            return result
        
        if response.get("isThinking") and not CONFIG["IS_THINKING"]:
            result["token"] = "<think>" + response.get("token", "")
            CONFIG["IS_THINKING"] = True
        elif not response.get("isThinking") and CONFIG["IS_THINKING"]:
            result["token"] = "</think>" + response.get("token", "")
            CONFIG["IS_THINKING"] = False
        else:
            result["token"] = response.get("token")
    
    return result

def handle_image_response(image_url):
    max_retries = 2
    retry_count = 0
    image_base64_response = None
    
    while retry_count < max_retries:
        try:
            proxy_options = Utils.get_proxy_options()
            image_base64_response = curl_requests.get(
                f"https://assets.grok.com/{image_url}",
                headers={
                    **DEFAULT_HEADERS,
                    "Cookie": CONFIG["API"]["SIGNATURE_COOKIE"]
                },
                impersonate="chrome120",
                **proxy_options
            )
            
            if image_base64_response.status_code == 200:
                break
                
            retry_count += 1
            if retry_count == max_retries:
                raise Exception(f"上游服务请求失败! status: {image_base64_response.status_code}")
                
            time.sleep(CONFIG["API"]["RETRY_TIME"] / 1000 * retry_count)
        
        except Exception as error:
            logger.error(str(error), "Server")
            retry_count += 1
            if retry_count == max_retries:
                raise
                
            time.sleep(CONFIG["API"]["RETRY_TIME"] / 1000 * retry_count)
    
    image_buffer = image_base64_response.content
    
    if not CONFIG["API"]["PICGO_KEY"] and not CONFIG["API"]["TUMY_KEY"]:
        base64_image = base64.b64encode(image_buffer).decode('utf-8')
        image_content_type = image_base64_response.headers.get('content-type', 'image/jpeg')
        return f"![image](data:{image_content_type};base64,{base64_image})"
    
    logger.info("开始上传图床", "Server")
    
    if CONFIG["API"]["PICGO_KEY"]:
        files = {'source': ('image.jpg', image_buffer, 'image/jpeg')}
        headers = {
            "X-API-Key": CONFIG["API"]["PICGO_KEY"]
        }
        
        response_url = requests.post(
            "https://www.picgo.net/api/1/upload",
            files=files,
            headers=headers
        )
        
        if response_url.status_code != 200:
            return "生图失败，请查看PICGO图床密钥是否设置正确"
        else:
            logger.info("生图成功", "Server")
            result = response_url.json()
            return f"![image]({result['image']['url']})"
    

    elif CONFIG["API"]["TUMY_KEY"]:
        files = {'file': ('image.jpg', image_buffer, 'image/jpeg')}
        headers = {
            "Accept": "application/json",
            'Authorization': f"Bearer {CONFIG['API']['TUMY_KEY']}"
        }
        
        response_url = requests.post(
            "https://tu.my/api/v1/upload",
            files=files,
            headers=headers
        )
        
        if response_url.status_code != 200:
            return "生图失败，请查看TUMY图床密钥是否设置正确"
        else:
            try:
                result = response_url.json()
                logger.info("生图成功", "Server")
                return f"![image]({result['data']['links']['url']})"
            except Exception as error:
                logger.error(str(error), "Server")
                return "生图失败，请查看TUMY图床密钥是否设置正确"


def handle_non_stream_response(response, model):
    try:
        content = response.text
        lines = content.split('\n')
        full_response = ""

        CONFIG["IS_THINKING"] = False
        CONFIG["IS_IMG_GEN"] = False
        CONFIG["IS_IMG_GEN2"] = False
        
        logger.info("开始处理非流式响应", "Server")
        
        for line in lines:
            if not line.strip():
                continue
            
            try:
                line_json = json.loads(line.strip())
                if line_json.get("error"):
                    logger.error(json.dumps(line_json, indent=2), "Server")
                    if line_json.get("error", {}).get("name") == "RateLimitError":
                        CONFIG["API"]["TEMP_COOKIE"] = None
                    raise ValueError("RateLimitError")
                
                response_data = line_json.get("result", {}).get("response")
                if not response_data:
                    continue
                
                if response_data.get("doImgGen") or response_data.get("imageAttachmentInfo"):
                    CONFIG["IS_IMG_GEN"] = True
                
                result = process_model_response(response_data, model)
                
                if result["token"]:
                    full_response += result["token"]
                
                if result["imageUrl"]:
                    CONFIG["IS_IMG_GEN2"] = True
                    return handle_image_response(result["imageUrl"])
            
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error(f"处理响应行时出错: {str(e)}", "Server")
                continue
        
        return full_response
    
    except Exception as error:
        logger.error(str(error), "Server")
        raise

def handle_stream_response(response, model):
    def generate():
        stream = response.iter_lines()

        CONFIG["IS_THINKING"] = False
        CONFIG["IS_IMG_GEN"] = False
        CONFIG["IS_IMG_GEN2"] = False
        logger.info("开始处理流式响应", "Server")

        for chunk in stream:
            if not chunk:
                continue
            try:
                line_json = json.loads(chunk.decode("utf-8").strip()) 
                if line_json.get("error"):
                    logger.error(json.dumps(line_json, indent=2), "Server")
                    yield json.dumps({"error": "RateLimitError"}) + "\n\n"
                    return
                
                response_data = line_json.get("result", {}).get("response")
                if not response_data:
                    continue
                
                if response_data.get("doImgGen") or response_data.get("imageAttachmentInfo"):
                    CONFIG["IS_IMG_GEN"] = True
                
                result = process_model_response(response_data, model)
                
                if result["token"]:
                    yield f"data: {json.dumps(MessageProcessor.create_chat_response(result['token'], model, True))}\n\n"
                
                if result["imageUrl"]:
                    CONFIG["IS_IMG_GEN2"] = True
                    image_data = handle_image_response(result["imageUrl"])
                    yield f"data: {json.dumps(MessageProcessor.create_chat_response(image_data, model, True))}\n\n"
            
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error(f"处理流式响应行时出错: {str(e)}", "Server")
                continue
    
        yield "data: [DONE]\n\n"
    return generate()

def initialization():
    sso_array = os.environ.get("SSO", "").split(',')
    logger.info("开始加载令牌", "Server")
    for sso in sso_array:
        if sso: 
            token_manager.add_token(f"sso-rw={sso};sso={sso}")
    
    logger.info(f"成功加载令牌: {json.dumps(token_manager.get_all_tokens(), indent=2)}", "Server")
    logger.info(f"令牌加载完成，共加载: {len(token_manager.get_all_tokens())}个令牌", "Server")
    
    if CONFIG["API"]["PROXY"]:
        logger.info(f"代理已设置: {CONFIG['API']['PROXY']}", "Server")
    
logger.info("初始化完成", "Server")


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)


@app.before_request
def log_request_info():
    logger.info(f"{request.method} {request.path}", "Request")

@app.route('/get/tokens', methods=['GET'])
def get_tokens():
    auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if CONFIG["API"]["IS_CUSTOM_SSO"]:
        return jsonify({"error": '自定义的SSO令牌模式无法获取轮询sso令牌状态'}), 403
    elif auth_token != CONFIG["API"]["API_KEY"]:
        return jsonify({"error": 'Unauthorized'}), 401
    
    return jsonify(token_manager.get_token_status_map())

@app.route('/add/token', methods=['POST'])
def add_token():
    auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if CONFIG["API"]["IS_CUSTOM_SSO"]:
        return jsonify({"error": '自定义的SSO令牌模式无法添加sso令牌'}), 403
    elif auth_token != CONFIG["API"]["API_KEY"]:
        return jsonify({"error": 'Unauthorized'}), 401
    
    try:
        sso = request.json.get('sso')
        token_manager.add_token(f"sso-rw={sso};sso={sso}")
        return jsonify(token_manager.get_token_status_map().get(sso, {})), 200
    except Exception as error:
        logger.error(str(error), "Server")
        return jsonify({"error": '添加sso令牌失败'}), 500

@app.route('/delete/token', methods=['POST'])
def delete_token():
    auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if CONFIG["API"]["IS_CUSTOM_SSO"]:
        return jsonify({"error": '自定义的SSO令牌模式无法删除sso令牌'}), 403
    elif auth_token != CONFIG["API"]["API_KEY"]:
        return jsonify({"error": 'Unauthorized'}), 401
    
    try:
        sso = request.json.get('sso')
        token_manager.delete_token(f"sso-rw={sso};sso={sso}")
        return jsonify({"message": '删除sso令牌成功'}), 200
    except Exception as error:
        logger.error(str(error), "Server")
        return jsonify({"error": '删除sso令牌失败'}), 500

@app.route('/v1/models', methods=['GET'])
def get_models():
    return jsonify({
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "grok"
            } 
            for model in CONFIG["MODELS"].keys()
        ]
    })

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    try:
        auth_token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if auth_token:
            if CONFIG["API"]["IS_CUSTOM_SSO"]:
                result = f"sso={auth_token};sso-rw={auth_token}"
                token_manager.set_token(result)
            elif auth_token != CONFIG["API"]["API_KEY"]:
                return jsonify({"error": 'Unauthorized'}), 401
        else:
            return jsonify({"error": 'API_KEY缺失'}), 401
        
        data = request.json
        model = data.get("model")
        stream = data.get("stream", False)
        
        retry_count = 0
        grok_client = GrokApiClient(model)
        request_payload = grok_client.prepare_chat_request(data)
        
        while retry_count < CONFIG["RETRY"]["MAX_ATTEMPTS"]:
            retry_count += 1
            CONFIG["API"]["SIGNATURE_COOKIE"] = Utils.create_auth_headers(model)
            
            if not CONFIG["API"]["SIGNATURE_COOKIE"]:
                raise ValueError('该模型无可用令牌')
            
            logger.info(f"当前令牌: {json.dumps(CONFIG['API']['SIGNATURE_COOKIE'], indent=2)}", "Server")
            logger.info(f"当前可用模型的全部可用数量: {json.dumps(token_manager.get_remaining_token_request_capacity(), indent=2)}", "Server")
            
            try:
                proxy_options = Utils.get_proxy_options()
                response = curl_requests.post(
                    f"{CONFIG['API']['BASE_URL']}/rest/app-chat/conversations/new",
                    headers={
                        "Accept": "text/event-stream",
                        "Baggage": "sentry-public_key=b311e0f2690c81f25e2c4cf6d4f7ce1c",
                        "Content-Type": "text/plain;charset=UTF-8",
                        "Connection": "keep-alive",
                        "Cookie": CONFIG["API"]["SIGNATURE_COOKIE"]
                    },
                    data=json.dumps(request_payload),
                    impersonate="chrome120",
                    stream=True,
                    **proxy_options
                )
                
                if response.status_code == 200:
                    logger.info("请求成功", "Server")
                    logger.info(f"当前{model}剩余可用令牌数: {token_manager.get_token_count_for_model(model)}", "Server")
                    
                    try:
                        if stream:
                            return Response(
                                stream_with_context(handle_stream_response(response, model)),
                                content_type='text/event-stream'
                            )
                        else:
                            content = handle_non_stream_response(response, model)
                            return jsonify(MessageProcessor.create_chat_response(content, model))
                    
                    except Exception as error:
                        logger.error(str(error), "Server")
                        if CONFIG["API"]["IS_CUSTOM_SSO"]:
                            raise ValueError(f"自定义SSO令牌当前模型{model}的请求次数已失效")
                        
                        token_manager.remove_token_from_model(model, CONFIG["API"]["SIGNATURE_COOKIE"])
                        if token_manager.get_token_count_for_model(model) == 0:
                            raise ValueError(f"{model} 次数已达上限，请切换其他模型或者重新对话")
                
                elif response.status_code == 429:
                    if CONFIG["API"]["IS_CUSTOM_SSO"]:
                        raise ValueError(f"自定义SSO令牌当前模型{model}的请求次数已失效")
                    
                    token_manager.remove_token_from_model(model, CONFIG["API"]["SIGNATURE_COOKIE"])
                    if token_manager.get_token_count_for_model(model) == 0:
                        raise ValueError(f"{model} 次数已达上限，请切换其他模型或者重新对话")
                
                else:
                    if CONFIG["API"]["IS_CUSTOM_SSO"]:
                        raise ValueError(f"自定义SSO令牌当前模型{model}的请求次数已失效")
                    
                    logger.error(f"令牌异常错误状态!status: {response.status_code}", "Server")
                    token_manager.remove_token_from_model(model, CONFIG["API"]["SIGNATURE_COOKIE"])
                    logger.info(f"当前{model}剩余可用令牌数: {token_manager.get_token_count_for_model(model)}", "Server")
            
            except Exception as e:
                logger.error(f"请求处理异常: {str(e)}", "Server")
                if CONFIG["API"]["IS_CUSTOM_SSO"]:
                    raise
                continue
        
        raise ValueError('当前模型所有令牌都已耗尽')
    
    except Exception as error:
        logger.error(str(error), "ChatAPI")
        return jsonify({
            "error": {
                "message": str(error),
                "type": "server_error"
            }
        }), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    return 'api运行正常', 200

if __name__ == '__main__':
    token_manager = AuthTokenManager()
    initialization()
    
    app.run(
        host='0.0.0.0',
        port=CONFIG["SERVER"]["PORT"],
        debug=False
    )
