"""Conservative local question routing; production uses a structured model assessment.

Scope means the capabilities of evidence-based QA. A topic missing from retrieval
is insufficient evidence, not an out-of-scope topic. This service has no chat history.
"""

from __future__ import annotations

import re

from ragkb.domain.rag import QuestionAssessment, QuestionDisposition


class ConservativeQuestionAssessor:
    revision = "conservative-question-assessor:v1"

    def assess(self, question: str) -> QuestionAssessment:
        query = question.strip()
        # Only explicit execution requests; questions about a procedure remain QA.
        if re.search(
            r"^(?:请|麻烦|能否|可以|你能|你可以|能不能|可不可以)*\s*(?:帮我|替我|为我)\s*"
            r"(?:预订|订购|购买|付款|转账|下单|发送|发邮件|删除账户|执行代码|运行程序)"
            r"|^(?:please\s+)?(?:book|buy|purchase|send|transfer|execute|run)\b.*\b(?:for me|my)\b",
            query,
            re.IGNORECASE,
        ):
            return QuestionAssessment(QuestionDisposition.OUT_OF_SCOPE, "unsupported_operation")
        if re.fullmatch(
            r"(?:它|他|她|这个|那个|该产品|该制度)(?:的)?(?:期限|保修期|价格|费用|规则|要求|政策)"
            r"(?:是|为|有|要|是多少|是多久|多久|多少|是什么|有哪些|怎么样|怎么规定)*[？?。.!！\s]*"
            r"|(?:how (?:much|long)|what) (?:is|are) (?:it|its|that|their)(?:[\w\s]*)[?.!]*",
            query,
            re.IGNORECASE,
        ):
            return QuestionAssessment(
                QuestionDisposition.NEEDS_CLARIFICATION, "missing_context", ("subject",)
            )
        return QuestionAssessment()
