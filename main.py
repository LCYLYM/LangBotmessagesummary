from pkg.plugin.context import register, handler, llm_func, BasePlugin, APIHost, EventContext
from pkg.plugin.events import *  # 导入事件类
import sqlite3
import datetime
import asyncio


# 注册插件
@register(name="ChatAnalyzer", description="群聊分析插件", version="0.1", author="生鱼")
class ChatAnalyzerPlugin(BasePlugin):

    # 初始化数据库
    def __init__(self, host: APIHost):
        self.db = sqlite3.connect('chat_records.db')
        self.cursor = self.db.cursor()
        # 创建消息记录表
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT,
            sender_id TEXT, 
            message TEXT,
            timestamp DATETIME
        )
        ''')
        self.db.commit()

    # 异步初始化 - 启动定时任务
    async def initialize(self):
        asyncio.create_task(self.daily_summary_task())

    # 记录所有群消息
    @handler(GroupNormalMessageReceived)
    async def on_group_message(self, ctx: EventContext):
        msg = ctx.event.text_message
        group_id = ctx.event.group_id
        sender_id = ctx.event.sender_id
        
        # 存储消息记录
        self.cursor.execute(
            'INSERT INTO messages (group_id, sender_id, message, timestamp) VALUES (?, ?, ?, ?)',
            (group_id, sender_id, msg, datetime.datetime.now())
        )
        self.db.commit()

    # 定时总结任务
    async def daily_summary_task(self):
        while True:
            now = datetime.datetime.now()
            # 等待到明天0点
            tomorrow = now.replace(hour=0, minute=0, second=0) + datetime.timedelta(days=1)
            await asyncio.sleep((tomorrow - now).seconds)
            
            # 获取所有群
            groups = self.get_all_groups()
            
            for group_id in groups:
                # 获取该群当天的消息
                messages = self.get_today_messages(group_id)
                if messages:
                    # 调用AI生成总结
                    summary = await self.generate_summary(messages)
                    # 发送总结到群
                    await self.send_group_message(group_id, summary)

    # 处理查看用户画像命令
    @handler(GroupNormalMessageReceived)
    async def on_profile_command(self, ctx: EventContext):
        msg = ctx.event.text_message
        if msg.startswith("看看"):
            target_id = msg[2:].strip() # 获取目标用户ID
            
            # 获取用户最近50条消息
            messages = self.get_user_messages(target_id, limit=50)
            if messages:
                # 生成用户画像
                profile = await self.generate_profile(messages)
                ctx.add_return("reply", [profile])
                ctx.prevent_default()

    # 当收到个人消息时触发
    @handler(PersonNormalMessageReceived)
    async def person_normal_message_received(self, ctx: EventContext):
        msg = ctx.event.text_message  # 这里的 event 即为 PersonNormalMessageReceived 的对象
        if msg == "hello":  # 如果消息为hello

            # 输出调试信息
            self.ap.logger.debug("hello, {}".format(ctx.event.sender_id))

            # 回复消息 "hello, <发送者id>!"
            ctx.add_return("reply", ["hello, {}!".format(ctx.event.sender_id)])

            # 阻止该事件默认行为（向接口获取回复）
            ctx.prevent_default()

    # 当收到群消息时触发
    @handler(GroupNormalMessageReceived)
    async def group_normal_message_received(self, ctx: EventContext):
        msg = ctx.event.text_message  # 这里的 event 即为 GroupNormalMessageReceived 的对象
        if msg == "hello":  # 如果消息为hello

            # 输出调试信息
            self.ap.logger.debug("hello, {}".format(ctx.event.sender_id))

            # 回复消息 "hello, everyone!"
            ctx.add_return("reply", ["hello, everyone!"])

            # 阻止该事件默认行为（向接口获取回复）
            ctx.prevent_default()

    def __del__(self):
        self.db.close()


    def get_all_groups(self):
        """获取所有有记录的群ID"""
        self.cursor.execute('SELECT DISTINCT group_id FROM messages')
        return [row[0] for row in self.cursor.fetchall()]

    def get_today_messages(self, group_id):
        """获取指定群当天的消息记录"""
        today = datetime.datetime.now().date()
        self.cursor.execute(
            '''SELECT sender_id, message, timestamp 
               FROM messages 
               WHERE group_id = ? AND date(timestamp) = ?
               ORDER BY timestamp''',
            (group_id, today)
        )
        return self.cursor.fetchall()

    def get_user_messages(self, user_id, limit=50):
        """获取指定用户的最近消息记录"""
        self.cursor.execute(
            '''SELECT message, timestamp 
               FROM messages 
               WHERE sender_id = ?
               ORDER BY timestamp DESC 
               LIMIT ?''',
            (user_id, limit)
        )
        return self.cursor.fetchall()

    async def generate_summary(self, messages):
        """生成群聊总结"""
        # 格式化消息记录
        msg_text = "\n".join([
            f"{msg[0]}: {msg[1]} ({msg[2]})" 
            for msg in messages
        ])
        
        # 调用AI生成总结
        prompt = f"""请总结以下群聊记录的主要内容,包括:
1. 主要讨论的话题
2. 重要的信息或决定
3. 整体聊天氛围
4. 活跃的成员

聊天记录:
{msg_text}"""
        
        # 使用插件API调用LLM
        response = await self.ap.llm.ask_llm(prompt)
        return f"【今日群聊总结】\n{response}"

    async def generate_profile(self, messages):
        """生成用户画像"""
        msg_text = "\n".join([
            f"{msg[0]} ({msg[1]})" 
            for msg in messages
        ])
        
        prompt = f"""请根据以下聊天记录分析该用户的特点:
1. 性格特征
2. 关注的话题
3. 表达方式
4. 群内角色

聊天记录:
{msg_text}"""
        
        response = await self.ap.llm.ask_llm(prompt)
        return f"【用户画像分析】\n{response}"

    async def send_group_message(self, group_id, message):
        """发送群消息"""
        try:
            # 使用插件API发送消息
            await self.ap.send_group_message(group_id, message)
        except Exception as e:
            self.ap.logger.error(f"发送群消息失败: {e}")
