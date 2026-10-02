"""业务服务层。

业务代码使用智能能力的统一入口：``from app.services.ai_service import AIService``。
任何模块都不得直接引入厂商 SDK、厂商 URL 或厂商模型名。
"""

from app.services.ai_service import AIService, ExtractedEvent, ExtractedEventResult, ai_available

__all__ = ["AIService", "ExtractedEvent", "ExtractedEventResult", "ai_available"]
