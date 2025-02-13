from pkg.plugin.context import register, handler, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *
from pkg.platform.types import *
from pkg.provider.entities import Message
import csv
import os
from datetime import datetime, timedelta
import traceback
import json
import asyncio
import aiofiles
import aiohttp
import openai
from aiohttp import web
import base64

@register(name="ChatAnalyzer", description="群聊分析插件", version="0.1", author="作者名")
class ChatAnalyzerPlugin(BasePlugin):

    def __init__(self, host: APIHost):
        super().__init__(host)
        try:
            # 设置数据目录
            self.data_dir = os.path.join('/app/data/chat_analyzer')
            print(f"数据目录设置为: {self.data_dir}")
            
            # 确保目录存在
            os.makedirs(self.data_dir, exist_ok=True)
            
            # 初始化文件路径
            self.init_paths()
            
            # 启动定时任务
            self.start_daily_task()
            
            # 确保模板目录存在
            template_dir = os.path.join(os.path.dirname(__file__), 'templates')
            static_dir = os.path.join(os.path.dirname(__file__), 'static')
            os.makedirs(template_dir, exist_ok=True)
            os.makedirs(static_dir, exist_ok=True)
            print(f"模板目录: {template_dir}")
            print(f"静态文件目录: {static_dir}")
            
            # 启动Web服务
            print("正在启动Web服务...")
            asyncio.create_task(self.start_web_server())
            print("Web服务启动任务已创建")
            
        except Exception as e:
            print(f"初始化插件时发生错误: {traceback.format_exc()}")

    def init_paths(self):
        """初始化各种文件路径"""
        current_date = datetime.now().strftime("%Y%m%d")
        # 每日消息记录
        self.daily_log_path = os.path.join(self.data_dir, f'daily_{current_date}.csv')
        # 用户画像数据
        self.user_profile_dir = os.path.join(self.data_dir, 'user_profiles')
        os.makedirs(self.user_profile_dir, exist_ok=True)
        # 总结历史记录
        self.summary_path = os.path.join(self.data_dir, 'summary.csv')
        
        print(f"日志文件路径: {self.daily_log_path}")
        
        # 如果日志文件不存在,创建并写入表头
        if not os.path.exists(self.daily_log_path):
            with open(self.daily_log_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp',
                    'group_id',
                    'group_name', 
                    'sender_id',
                    'sender_name',
                    'text_message',
                    'raw_data'
                ])
            print(f"创建新的日志文件: {self.daily_log_path}")
            
        # 如果总结文件不存在,创建并写入表头    
        if not os.path.exists(self.summary_path):
            with open(self.summary_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp',
                    'group_id',
                    'summary_type',  # auto/manual
                    'content',
                    'source_date'
                ])
            print(f"创建新的总结文件: {self.summary_path}")

    def start_daily_task(self):
        """启动每日定时任务"""
        async def daily_task():
            while True:
                now = datetime.now()
                # 计算下一个凌晨0点
                next_run = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                # 等待到下一次执行时间
                await asyncio.sleep((next_run - now).total_seconds())
                
                try:
                    # 执行每日总结
                    await self.daily_summary()
                    # 更新文件路径
                    self.init_paths()
                except Exception as e:
                    print(f"执行每日任务时出错: {traceback.format_exc()}")

        asyncio.create_task(daily_task())
        print("已启动每日定时任务")

    async def get_chat_history(self, group_id, limit=100):
        """获取群聊历史记录"""
        messages = []
        try:
            if not os.path.exists(self.daily_log_path):
                print(f"日志文件不存在: {self.daily_log_path}")
                return messages

            async with aiofiles.open(self.daily_log_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                for row in csv.DictReader(content.splitlines()):
                    if row['group_id'] == group_id:
                        messages.append(row)
                        if len(messages) >= limit:
                            break
            print(f"获取到 {len(messages)} 条群聊记录")
        except Exception as e:
            print(f"读取历史记录错误: {traceback.format_exc()}")
        return messages

    async def get_user_messages(self, group_id, user_id, limit=50):
        """获取用户的历史消息"""
        messages = []
        try:
            if not os.path.exists(self.daily_log_path):
                print(f"日志文件不存在: {self.daily_log_path}")
                return messages

            async with aiofiles.open(self.daily_log_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                for row in csv.DictReader(content.splitlines()):
                    if row['group_id'] == group_id and row['sender_id'] == user_id:
                        messages.append(row)
                        if len(messages) >= limit:
                            break
            print(f"获取到 {len(messages)} 条用户消息")
        except Exception as e:
            print(f"读取用户消息错误: {traceback.format_exc()}")
        return messages

    async def summarize_messages(self, messages, prompt_type="daily"):
        """使用AI总结消息"""
        try:
            if not messages:
                return "没有找到需要总结的消息"
                
            # 构建提示词
            if prompt_type == "daily":
                system_prompt = """你是一个群聊分析助手，负责总结群聊内容。请按以下要求输出：
1. 不要使用markdown语法
2. 使用适当的emoji表情
3. 分段输出，每段之间空一行
4. 按以下结构深入分析内容：

📋 今日话题概述
- 详细列举主要讨论话题
- 分析话题的发展脉络
- 总结核心议题走向

💡 重点讨论内容
- 深入分析各话题的讨论要点
- 总结群成员的主要观点分布
- 提炼有价值的信息亮点

🌈 群聊氛围分析
- 整体情感倾向分析
- 互动热度和活跃度评估
- 群组文化特征观察
- 特殊事件或话题影响

👥 成员互动特点
- 成员参与度分布
- 互动模式和规律
- 意见领袖表现
- 群体凝聚力表现

请确保：
- 每个部分都有充分详实的内容
- 分析要有深度和洞察
- 语言自然流畅
- 结构清晰易读"""

                user_prompt = "请深入分析以下群聊记录:\n\n"
            else:  # user_profile
                system_prompt = """你是一个群聊分析助手，负责分析用户画像。请按以下要求输出：
1. 不要使用markdown语法
2. 使用适当的emoji表情
3. 分段输出，每段之间空一行
4. 按以下结构深入分析用户特征：

🧠 思维特征分析
- 思维方式和逻辑特点
- 观点形成和表达方式
- 问题解决倾向
- 认知风格特点

💭 性格特征画像
- 性格特点全面分析
- 情感表达特征
- 核心价值观表现
- 行为模式特点

💬 表达风格特征
- 语言表达特点
- 用词和表达习惯
- 情感表达方式
- 沟通策略分析

🎯 兴趣和专业领域
- 主要关注话题
- 专业知识领域
- 兴趣爱好表现
- 观点倾向分析

🤝 社交互动模式
- 群内角色定位
- 社交风格特点
- 互动习惯分析
- 人际关系处理

请确保：
- 分析要全面且深入
- 举例说明具体表现
- 注意分析的逻辑性
- 保持客观专业态度"""

                user_prompt = "请深入分析该用户的特征:\n\n"
                
            # 构建消息历史
            for msg in messages:
                user_prompt += f"[{msg.get('timestamp', '未知时间')}] {msg.get('sender_name', '未知用户')}: {msg['text_message']}\n"
            
            print(f"AI提示词: {user_prompt}")
            
            # 构建请求消息
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            # 创建异步OpenAI客户端
            client = openai.AsyncOpenAI(
                base_url="YOUR_API_BASE_URL",
                api_key="YOUR_API_KEY"
            )

            # 添加重试机制
            max_retries = 3
            retry_delay = 1
            
            for attempt in range(max_retries):
                try:
                    response = await client.chat.completions.create(
                        model="gemini-2.0-flash",
                        messages=messages,
                        temperature=0.7,
                        max_tokens=2000  # 增加token限制以允许更长回复
                    )
                    
                    if response and response.choices and response.choices[0].message:
                        return response.choices[0].message.content
                        
                    print(f"API返回无效响应,重试中({attempt + 1}/{max_retries})")
                    
                except Exception as e:
                    print(f"API调用出错: {traceback.format_exc()}")
                    print(f"重试中({attempt + 1}/{max_retries})")
                    
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    
            return "AI未能生成有效响应,请稍后重试"
            
        except Exception as e:
            print(f"AI总结错误: {traceback.format_exc()}")
            return "AI总结过程中发生错误"

    async def daily_summary(self):
        """执行每日总结"""
        try:
            print("开始执行每日总结")
            
            # 获取所有群组
            groups = set()
            if os.path.exists(self.daily_log_path):
                async with aiofiles.open(self.daily_log_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    for row in csv.DictReader(content.splitlines()):
                        groups.add(row['group_id'])
            
            print(f"发现 {len(groups)} 个群")
            
            # 为每个群生成总结
            for group_id in groups:
                messages = await self.get_chat_history(group_id)
                if messages:
                    summary = await self.summarize_messages(messages)
                    # 保存自动总结
                    await self.save_summary(group_id, summary, 'auto')
                    
                    # 构造飞书webhook消息
                    webhook_url = "YOUR_FEISHU_WEBHOOK_URL"
                    webhook_data = {
                        "msg_type": "text",
                        "content": {
                            "text": f"群 {group_id} 的每日总结:\n{summary}"
                        }
                    }
                    
                    # 发送webhook请求
                    async with aiohttp.ClientSession() as session:
                        async with session.post(webhook_url, json=webhook_data) as resp:
                            if resp.status == 200:
                                print(f"已发送群 {group_id} 的每日总结到飞书")
                            else:
                                print(f"发送群 {group_id} 的每日总结到飞书失败: {await resp.text()}")

        except Exception as e:
            print(f"每日总结错误: {traceback.format_exc()}")

    @handler(GroupNormalMessageReceived)
    async def on_group_message(self, ctx: EventContext):
        """处理群消息"""
        try:
            # 立即阻止默认行为
            ctx.prevent_default()
            
            # 获取消息信息
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            group_id = getattr(ctx.event, 'launcher_id', '')
            sender_id = getattr(ctx.event, 'sender_id', '')
            text = getattr(ctx.event, 'text_message', '')
            
            print(f"收到群消息: {timestamp} | 群:{group_id} | 发送者:{sender_id} | 内容:{text}")
            
            # 处理!开头的命令
            if text.startswith('!'):
                # 只有指定群可以响应!命令
                if group_id != 'YOUR_GROUP_ID':
                    return
                    
            # 处理其他命令
            if text == '总结':
                print("收到总结命令")
                # 获取群历史消息
                messages = await self.get_chat_history(group_id)
                print(f"获取到 {len(messages)} 条历史消息")
                # 生成总结
                summary = await self.summarize_messages(messages)
                print(f"生成总结: {summary}")
                # 保存总结
                await self.save_summary(group_id, summary, 'manual')
                # 发送总结
                await ctx.reply(MessageChain([Plain(f"【群聊总结】\n{summary}")]))
                return
                
            elif text.startswith('看看'):
                print("收到看看命令")
                # 解析用户ID
                user_id = text.split(' ')[1] if len(text.split(' ')) > 1 else sender_id
                print(f"目标用户ID: {user_id}")
                # 获取用户消息
                messages = await self.get_user_messages(group_id, user_id)
                print(f"获取到 {len(messages)} 条用户消息")
                # 生成画像
                profile = await self.summarize_messages(messages, "user_profile")
                print(f"生成画像: {profile}")
                # 保存总结
                await self.save_summary(group_id, profile, 'manual')
                # 发送画像
                await ctx.reply(MessageChain([Plain(f"【用户画像】\n{profile}")]))
                return
                
            # 记录消息
            raw_data = {
                'timestamp': timestamp,
                'group_id': group_id,
                'sender_id': sender_id,
                'text': text
            }
            
            # 写入CSV
            async with aiofiles.open(self.daily_log_path, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                await writer.writerow([
                    timestamp,
                    group_id,
                    '',  # group_name
                    sender_id,
                    '',  # sender_name
                    text,
                    json.dumps(raw_data, ensure_ascii=False)
                ])
                
        except Exception as e:
            print(f"处理消息错误: {traceback.format_exc()}")

    # === Web服务相关代码开始 ===
    
    async def start_web_server(self):
        """启动Web服务器"""
        try:
            print("开始配置Web服务器...")
            
            # 创建中间件来处理认证
            @web.middleware
            async def auth_middleware(request, handler):
                # 检查认证
                auth = request.headers.get('Authorization')
                if not auth:
                    # 没有认证信息，返回401要求认证
                    headers = {'WWW-Authenticate': 'Basic realm="Restricted Access"'}
                    return web.Response(status=401, headers=headers)
                
                try:
                    # 解码认证信息
                    auth_decoded = base64.b64decode(auth.split()[1]).decode('utf-8')
                    username, password = auth_decoded.split(':')
                    if password != "YOUR_PASSWORD":  # 设置密码
                        raise ValueError("密码错误")
                except:
                    # 认证失败，返回401
                    headers = {'WWW-Authenticate': 'Basic realm="Restricted Access"'}
                    return web.Response(status=401, headers=headers)
                
                # 认证成功，继续处理请求
                return await handler(request)
            
            app = web.Application(middlewares=[auth_middleware])
            app.router.add_get('/', self.handle_index)
            app.router.add_get('/messages', self.handle_messages)
            app.router.add_get('/summaries', self.handle_summaries)
            
            static_path = os.path.join(os.path.dirname(__file__), 'static')
            app.router.add_static('/static', static_path)
            print(f"静态文件路径配置为: {static_path}")
            
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', 3300)  # 使用3300端口
            await site.start()
            print("Web服务器已成功启动在 http://0.0.0.0:3300")
        except Exception as e:
            print(f"启动Web服务器时发生错误: {traceback.format_exc()}")

    async def handle_index(self, request):
        """处理首页请求"""
        try:
            template_path = os.path.join(os.path.dirname(__file__), 'templates', 'index.html')
            print(f"尝试加载模板: {template_path}")
            if not os.path.exists(template_path):
                print(f"警告: 模板文件不存在: {template_path}")
                return web.Response(text="Template not found", status=404)
            return web.FileResponse(template_path)
        except Exception as e:
            print(f"处理首页请求时发生错误: {traceback.format_exc()}")
            return web.Response(text="Internal Server Error", status=500)

    async def handle_messages(self, request):
        """处理消息列表请求"""
        try:
            group_id = request.query.get('group_id', '')
            date = request.query.get('date', datetime.now().strftime("%Y%m%d"))
            
            messages = []
            log_path = os.path.join(self.data_dir, f'daily_{date}.csv')
            
            if os.path.exists(log_path):
                async with aiofiles.open(log_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    for row in csv.DictReader(content.splitlines()):
                        if not group_id or row['group_id'] == group_id:
                            messages.append(row)
            
            return web.json_response(messages)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def handle_summaries(self, request):
        """处理总结列表请求"""
        try:
            group_id = request.query.get('group_id', '')
            summary_type = request.query.get('type', '')
            
            summaries = []
            if os.path.exists(self.summary_path):
                async with aiofiles.open(self.summary_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    for row in csv.DictReader(content.splitlines()):
                        if (not group_id or row['group_id'] == group_id) and \
                           (not summary_type or row['summary_type'] == summary_type):
                            summaries.append(row)
            
            return web.json_response(summaries)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def save_summary(self, group_id: str, content: str, summary_type: str = 'manual'):
        """保存总结内容"""
        try:
            async with aiofiles.open(self.summary_path, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                await writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    group_id,
                    summary_type,
                    content,
                    datetime.now().strftime('%Y%m%d')
                ])
            print(f"已保存{summary_type}总结到文件")
        except Exception as e:
            print(f"保存总结错误: {traceback.format_exc()}")

    # === Web服务相关代码结束 ===
