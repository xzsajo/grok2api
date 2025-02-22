
# grok2API 接入指南：基于 Docker 的实现

## 项目简介
本项目提供了一种简单、高效的方式通过 Docker 部署 使用openAI的格式转换调用grok官网，进行api处理。
## 方法一：Docker部署

### 1. 获取项目
克隆我的仓库：[grok2api](https://github.com/xLmiler/grok2api)
### 2. 部署选项

#### 方式A：直接使用Docker镜像
```bash
docker run -it -d --name grok2api \
  -p 3000:3000 \
  -e API_KEY=your_api_key \
  -e ISSHOW_SEARCH_RESULTS=false \
  -e CHROME_PATH=/usr/bin/chromium \
  -e PORT=3000 \
  -e SSO=your_sso \
  -e SSO_RW=your_sso_rw \
  yxmiler/grok2api:latest
```

#### 方式B：使用Docker Compose
````artifact
version: '3.8'
services:
  grok2api:
    image: yxmiler/grok2api:latest
    container_name: grok2api
    ports:
      - "3000:3000"
    environment:
      - API_KEY=your_api_key
      - ISSHOW_SEARCH_RESULTS=false
      - CHROME_PATH=/usr/bin/chromium
      - PORT=3000
      - SSO=your_sso
      - SSO_RW=your_sso_rw
    restart: unless-stopped
````

#### 方式C：自行构建
1. 克隆仓库
2. 构建镜像
```bash
docker build -t yourusername/grok2api .
```
3. 运行容器
```bash
docker run -it -d --name grok2api \
  -p 3000:3000 \
  -e API_KEY=your_api_key \
  -e ISSHOW_SEARCH_RESULTS=false \
  -e CHROME_PATH=/usr/bin/chromium \
  -e PORT=3000 \
  -e SSO=your_sso \
  -e SSO_RW=your_sso_rw \
  yourusername/grok2api:latest
```

### 3. 环境变量配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `API_KEY` | 自定义认证鉴权密钥 | `sk-123456` |
| `PICGO_KEY` | PicGo图床密钥 | - |
| `ISSHOW_SEARCH_RESULTS` | 是否显示搜索结果 | `true/false` |
| `SSO` | Grok官网SSO Cookie,,可以设置多个使用,分隔 | - |
| `SSO_RW` | Grok官网SSO_RW Cookie,,可以设置多个使用,分隔 | - |
| `PORT` | 服务部署端口 | `3000` |
| `CHROME_PATH` | 谷歌浏览器路径,无特别需要不需要修改 | `/usr/bin/chromium` |

## 方法二：Hugging Face部署

### 部署地址
https://huggingface.co/spaces/yxmiler/GrokAPIService

### 功能特点
实现的功能：
1. 已支持文字生成图，使用grok-2-imageGen和grok-3-imageGen模型。
2. 已支持全部模型识图和传图，只会识别存储用户消息最新的一个图，历史记录图全部为占位符替代。
3. 已支持搜索功能，使用grok-2-search模型，默认关闭搜索结果
4. 已支持深度搜索功能，使用grok-3-deepsearch
5. 已支持推理模型功能，使用grok-3-reasoning
6. 已支持真流式，上面全部功能都可以在流式情况调用
7. 支持多账号轮询，在环境变量中配置
8. grok2采用临时账号机制，理论无限调用。
9. 已转换为openai格式。

### 可用模型列表
- `grok-2`
- `grok-2-imageGen`
- `grok-2-search`
- `grok-3`
- `grok-3-imageGen`
- `grok-3-deepsearch`
- `grok-3-reasoning`
- 
### cookie的获取办法：
1、打开[grok官网](https://grok.com/)
2、复制如下两个填入即可
![A0{AOZO}2ESW5}8DYW)KSF6|690x220](upload://41LZb37nXdhWuvo4LRcE7JqBCkY.png)

### API调用

#### Docker版本
- 模型列表：`/v1/models`
- 对话：`/v1/chat/completions`

#### Hugging Face版本
- 模型列表：`/hf/v1/models`
- 对话：`/hf/v1/chat/completions`

## 备注
- 消息基于用户的伪造连续对话
- 可能存在一定程度的智能降级
## 补充说明
如需使用图像功能，需在[PicGo图床](https://www.picgo.net/)申请API Key。

## 注意事项
⚠️ 本项目仅供学习和研究目的，请遵守相关使用条款。

