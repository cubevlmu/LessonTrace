"""Bounded structured writing over a backend-authorized classroom packet."""

import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict, deque
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .skills import ACADEMIC_SOURCE_KINDS, ANALYSIS_KB, KB_VERSION, MINIMAL_SYSTEM_PROMPT, VIDEO_SOURCE_KINDS

Id = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_REF_FIELDS = ("evidence_refs", "relation_refs", "query_refs", "change_event_refs")
_MAX_STATEMENTS = 24
_MAX_QUERIES = 2


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement_id: Id
    role: Literal["fact", "analysis", "recommendation", "coverage_note", "change_note"]
    section_id: Id
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    evidence_refs: list[Id] = Field(default_factory=list, max_length=32)
    depends_on_statements: list[Id] = Field(default_factory=list, max_length=_MAX_STATEMENTS)
    relation_refs: list[Id] = Field(default_factory=list, max_length=32)
    query_refs: list[Id] = Field(default_factory=list, max_length=32)
    change_event_refs: list[Id] = Field(default_factory=list, max_length=32)


class NeedMoreQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["get_passage", "get_rubric_item", "get_student_work", "get_video_window"]
    args: dict


class StatementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statements: list[Statement] = Field(max_length=_MAX_STATEMENTS)
    need_more: list[NeedMoreQuery] = Field(default_factory=list, max_length=_MAX_QUERIES)


_CONTRACT = """只输出 JSON 对象 {statements: [...], need_more: [...]}。每条 statement 有
statement_id（本稿内唯一的临时 ID）、role（fact、analysis、recommendation、coverage_note、
change_note 之一）、section_id、text、evidence_refs、depends_on_statements、relation_refs、
query_refs、change_event_refs。引用只能用本轮提供的对应 ID，教研方法不是证据。依赖只指本稿
其他判断；用于正文的学业判断要直接引用本人该题的作答、测评或批改。覆盖说明可引用查找结果，
没有出处就只说目前不能判断什么。输入有冲突时，不混同
对象、时段和来源，说明有哪一点对不上，不用一份来源替另一份。正文不写内部 ID，不写
“综合分析”“建议如下”这类小标题或开场白；普通问答把判断写成可接起来的 1–3 段自然话。
只在缺少会改变答案的资料时补取，最多两项，每项为 {tool, args}，工具限 get_passage、
get_rubric_item、get_student_work、get_video_window；尽量给出已有定位。补取关闭时 need_more 给空数组。"""

# Bounded clause checks are a backstop for the prompt, not a claim of entailment.
_CLAUSES = re.compile(r"[,，。；;！？!?\n]|(?<!\d)\.|但是|但|然而|不过|\b(?:but|however)\b", re.I)
_MENTAL = re.compile(
    r"心理|专注|专心|走神|分心|注意力|情绪|学习态度|学习动机|学习兴趣|认知状态|"
    r"认真(?:听讲|听课|学习)|心不在焉|睡着|在睡觉|已经入睡|"
    r"\b(?:attent(?:ion|ive)|inattentive|engagement|"
    r"distracted|emotion\w*|motivation|asleep|sleeping)\b", re.I,
)
_ACADEMIC = re.compile(
    r"掌握|薄弱|短板|学会|学懂|听(?:不|没)?懂|理解(?:了|得|透|到位|不到位|不清|不足|不够)|"
    r"不理解|没理解|懂了|没懂|弄明白|弄清楚|不会做|已会做|学业水平|学习能力|学习效果|"
    r"知识(?:点)?达标|"
    r"\b(?:master(?:y|ed)|underst(?:ands?|ood|anding)|learning outcomes?|"
    r"proficiency|weakness)\b", re.I,
)
_GLOBAL = re.compile(
    r"总体|整体|全部|所有|全(?:科|程|面)|整(?:门|节课)|学业水平|学习能力|学习效果|"
    r"(?:数学|语文|英语)能力|\b(?:overall|general ability|all topics)\b", re.I,
)
_ITEM_SCOPE = re.compile(
    r"第\s*[0-9一二三四五六七八九十]+\s*(?:小?题|问|步)|"
    r"(?:该|本|这|那)(?:道|一)?(?:题|步|小问)|同题|"
    r"对[^，。]{2,24}(?:的(?:理解|掌握)|掌握|较薄弱)|"
    r"在[^，。]{2,24}(?:上|方面)|(?:掌握|学会)(?:了)?(?!(?:得|的|很好|不牢|不好))"
    r"[A-Za-z\u3400-\u9fff]{2,12}|\b(?:item|question|problem|step)\s*\d+\b", re.I,
)
_VISUAL_BASIS = re.compile(r"画面|视频|录像|镜头|低头|抬头|停笔|翻页|姿势|监控|\b(?:video|footage)\b", re.I)
_WORK_BASIS = re.compile(r"作答|原答|答卷|卷面|答题|批改|测评|作业|订正|题解|\b(?:submission|assessment)\b", re.I)
_NO_INFERENCE = re.compile(
    r"(?:不能|无法|不(?:能)?足以|不可|不宜|不能据此|不代表|不等于|不意味着|"
    r"尚不能|没有证据|不据此|仅凭.+?难以|难以|未能|尚无依据|看不出|看不出来|看看是否|核对是否|"
    r"检查是否|确认是否|想看看).{0,45}$|"
    r"(?:cannot|can't|does not|do not|insufficient(?: to)?|not enough evidence(?: to)?|"
    r"check whether)\b.{0,55}$", re.I,
)
_LIMIT_AFTER = re.compile(r"^(?:与否|程度|情况)?(?:仍|尚)?(?:无法|不能|不宜|难以|未能|不确定)")
_UNCITED_COVERAGE = re.compile(
    r"(?:我)?(?:目前|现在|现有(?:资料|记录)|手头(?:的)?(?:资料|记录))?"
    r"(?:还|仍|暂时|暂|尚)?(?:不足以|无法|不能|难以|看不出|看不出来)(?:判断|确定|核实|确认|看出)"
    r"[^，。；;！？!?\n]+[。！？]?$"
)
_BOILERPLATE = re.compile(
    r"^\s*[#*\s]*(?:[一二三四五][、.]\s*)?"
    r"(?:作为(?:一名|一个)?\s*AI|综上所述|综合分析|现状分析|问题诊断|改进措施|改进建议|教学建议|建议如下)"
    r"(?:[*#\s]*[:：、,，。\n]|\s*$)", re.I,
)


def _kinds(source):
    provenance = source.get("provenance")
    nested = provenance.get("source_kind") if isinstance(provenance, Mapping) else None
    return {x for x in (source.get("source_kind"), source.get("source_type"),
                        source.get("evidence_type"), source.get("kind"), nested) if isinstance(x, str)}


def _admit_sources(index, rows, reserved):
    if isinstance(rows, Mapping):
        rows = [rows]
    if not isinstance(rows, (list, tuple)):
        return 0
    added = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ref = row.get("evidence_id")
        if (not isinstance(ref, str) or not ref or ref.strip() != ref or ref in index
                or ref in reserved or ref.casefold().startswith("analysis_kb")
                or row.get("non_evidence") or "analysis_kb" in _kinds(row)):
            continue
        index[ref] = dict(row)
        added += 1
    return added


def _affirmed(pattern, clause):
    return any(
        not _NO_INFERENCE.search(clause[:match.start()])
        and not _LIMIT_AFTER.match(clause[match.end():])
        for match in pattern.finditer(clause)
    )


def _semantic_violation(text, refs, sources):
    source_kinds = [_kinds(sources[ref]) for ref in refs]
    has_work = any(kinds & ACADEMIC_SOURCE_KINDS and not kinds & VIDEO_SOURCE_KINDS for kinds in source_kinds)
    has_video = any(kinds & VIDEO_SOURCE_KINDS for kinds in source_kinds)
    basis = None
    item_scope = False
    for clause in _CLAUSES.split(text):
        visual, work = _VISUAL_BASIS.search(clause), _WORK_BASIS.search(clause)
        if visual:
            basis = "video"
            item_scope = False
        elif work:
            basis = "work"
        item_scope = item_scope or bool(_ITEM_SCOPE.search(clause))
        if _affirmed(_MENTAL, clause):
            return True
        if _affirmed(_ACADEMIC, clause):
            if not has_work or not item_scope or _GLOBAL.search(clause) or (has_video and basis == "video"):
                return True
    return False


def _stable_statement(statement, dependencies, scope):
    payload = statement.model_dump(exclude={"statement_id", "depends_on_statements"})
    for field in _REF_FIELDS:
        payload[field] = sorted(set(payload[field]))
    canonical = {**payload, "scope": scope, "depends_on_statements": sorted(set(dependencies))}
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode())
    return statement.model_copy(update={
        "statement_id": "stmt_" + digest.hexdigest()[:32],
        "depends_on_statements": list(dict.fromkeys(dependencies)),
        **{field: list(dict.fromkeys(getattr(statement, field))) for field in _REF_FIELDS},
    })


def _valid_statements(rows, allowed, sources, scope, issues):
    candidates = []
    for row in rows[:_MAX_STATEMENTS]:
        try:
            candidates.append(Statement.model_validate(row))
        except ValidationError:
            issues.append("statement_schema")
    if len(rows) > _MAX_STATEMENTS:
        issues.append("too_many_statements")
    repeats = Counter(s.statement_id for s in candidates)
    eligible = {}
    for s in candidates:
        if repeats[s.statement_id] > 1:
            issues.append("duplicate_statement_id")
        elif any(not set(getattr(s, f)) <= allowed[f] for f in _REF_FIELDS):
            issues.append("unknown_reference")
        elif s.role == "fact" and not s.evidence_refs:
            issues.append("missing_fact_source")
        elif s.role == "change_note" and not s.change_event_refs:
            issues.append("missing_change_event")
        elif s.role == "coverage_note" and not (s.evidence_refs or s.query_refs) and not _UNCITED_COVERAGE.fullmatch(s.text):
            issues.append("missing_coverage_source")
        elif _semantic_violation(s.text, s.evidence_refs, sources):
            issues.append("semantic_boundary")
        elif _BOILERPLATE.match(s.text):
            issues.append("boilerplate")
        else:
            eligible[s.statement_id] = s

    # Admit a statement only after all its dependencies have been admitted.
    # A cycle, a missing dependency, or a rejected ancestor cannot ground new text.
    blockers = {key: set(s.depends_on_statements) for key, s in eligible.items()}
    followers = defaultdict(list)
    for key, deps in blockers.items():
        for dep in deps:
            followers[dep].append(key)
    ready = deque(key for key, deps in blockers.items() if not deps)
    validated = {}
    while ready:
        key = ready.popleft()
        s = eligible[key]
        if (s.role in {"analysis", "recommendation"} and not s.evidence_refs
                and not any(validated[dep][1] for dep in s.depends_on_statements)):
            issues.append("missing_judgment_source")
            continue
        grounded = bool(s.evidence_refs) or any(validated[dep][1] for dep in s.depends_on_statements)
        stable = _stable_statement(s, [validated[dep][0].statement_id for dep in s.depends_on_statements], scope)
        validated[key] = (stable, grounded)
        for follower in followers[key]:
            blockers[follower].discard(key)
            if not blockers[follower]:
                ready.append(follower)

    issues.extend("unresolved_statement_dependency" for deps in blockers.values() if deps)
    result = {}
    for key in eligible:
        if key in validated:
            s, _ = validated[key]
            result.setdefault(s.statement_id, s)
    return list(result.values())


def _parse(value, allowed, sources, scope, issues):
    """Recover valid rows even when a sibling row or the query list is malformed."""
    if isinstance(value, StatementDraft):
        value = value.model_dump()
    elif isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    if not isinstance(value, Mapping):
        issues.append("draft_schema")
        return [], []
    rows = value.get("statements")
    if not isinstance(rows, list) or set(value) - {"statements", "need_more"}:
        issues.append("draft_schema")
    statements = _valid_statements(rows, allowed, sources, scope, issues) if isinstance(rows, list) else []
    queries = value.get("need_more", [])
    if not isinstance(queries, list) or len(queries) > _MAX_QUERIES:
        issues.append("need_more_schema_or_budget")
        return statements, []
    admitted = []
    seen = set()
    for row in queries:
        try:
            query = NeedMoreQuery.model_validate(row)
            key = json.dumps(query.model_dump(), sort_keys=True, allow_nan=False)
        except (ValueError, TypeError):
            issues.append("need_more_schema")
            continue
        if key not in seen:
            admitted.append(query)
            seen.add(key)
    return statements, admitted


def _messages(packet, sources, allowed, skill, methods, allow_queries):
    data = {k: v for k, v in packet.items() if k not in {"sources", "evidence"}}
    data.update({f: sorted(allowed[f]) for f in _REF_FIELDS if f != "evidence_refs"})
    data["sources"] = list(sources.values())
    json_schema = StatementDraft.model_json_schema()
    refs = json_schema["$defs"]["Statement"]["properties"]
    for field in _REF_FIELDS:
        if allowed[field]:
            refs[field]["items"] = {"type": "string", "enum": sorted(allowed[field])}
        else:
            refs[field]["maxItems"] = 0
    if not allow_queries:
        json_schema["properties"]["need_more"]["maxItems"] = 0
    closing = "本轮可按需补取。" if allow_queries else "本轮不补取，need_more 必须为空。"
    guidance = "\n".join(method.guidance for method in methods)
    system = "\n\n".join(part for part in (
        MINIMAL_SYSTEM_PROMPT, skill.prompt,
        "教研方法（仅写作指导，不是课堂事实）：\n" + guidance if guidance else "",
        _CONTRACT, closing,
    ) if part)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False, allow_nan=False)},
    ], json_schema


def _provider_writer(provider):
    write = getattr(provider, "write", None)
    if callable(write):
        return write
    # Legacy DeepSeek selection is anonymous-only. Full prose is allowed only
    # under the same explicit text-transfer setting as the compatible endpoint.
    from .text import DeepSeekProvider
    if isinstance(provider, DeepSeekProvider):
        config = getattr(provider, "config", {})
        if config.get("allow_text_transfer") is True:
            from .compatible import CompatibleProvider
            return CompatibleProvider({
                "model": provider.model,
                "base_url": "https://api.deepseek.com/v1",
                "api_key": config.get("api_key") or os.getenv("DEEPSEEK_API_KEY"),
                "max_tokens": config.get("max_tokens", 2600),
                "timeout": config.get("timeout", 120),
            }, transport=provider.transport).write
    return None


def _fact_excerpts(sources, scope):
    result = []
    for ref, row in sources.items():
        content = row.get("content") or row.get("text")
        kinds = _kinds(row)
        if not isinstance(content, str) or not content.strip() or "knowledge_rule" in kinds:
            continue
        excerpt = content.strip()
        if len(excerpt) > 220:
            excerpt = excerpt[:220] + "…"
        if kinds & VIDEO_SOURCE_KINDS and _semantic_violation(excerpt, [ref], sources):
            continue
        title = row.get("title")
        label = f"《{title.strip()[:80]}》" if isinstance(title, str) and title.strip() else "这条记录"
        if row.get("is_example"):
            label = "演示资料" + label if label != "这条记录" else "这条演示记录"
        s = Statement(statement_id="excerpt", role="fact", section_id="answer",
                      text=f"{label}里写的是：“{excerpt}”", evidence_refs=[ref])
        result.append(_stable_statement(s, [], scope))
        if len(result) == 3:
            break
    return result


def write_statements(provider, packet, skill, methods, timeout, tool_executor=None, max_calls=2):
    """Return (StatementDraft, audit) without persisting or publishing anything.

    The packet is an authorized, version-checked text snapshot with a `sources`
    list (`evidence_id`, source kind/type, and `content`), plus optional lists
    of admitted relation/query/change IDs. The existing `evidence` list is also
    accepted. The callable executor takes (tool, args) and returns one or more
    source rows. It must enforce read-only access, the same scope and current
    versions; model arguments are never authorization.
    """
    started = time.perf_counter()
    budget = max(0, min(2, max_calls))
    methods = tuple(methods)
    sources = {}
    reserved = {*ANALYSIS_KB, *(method.method_id for method in methods)}
    _admit_sources(sources, packet.get("sources", packet.get("evidence", [])), reserved)
    allowed = {"evidence_refs": sources.keys()}
    for field in _REF_FIELDS[1:]:
        ids = packet.get(field, [])
        allowed[field] = {ref for ref in ids if isinstance(ref, str)} if isinstance(ids, (list, tuple)) else set()
    scope = packet.get("scope_key", packet.get("requested_scope"))
    issues = []
    audit = {
        "skill_id": skill.skill_id,
        "kb_version": KB_VERSION,
        "method_ids": [method.method_id for method in methods],
        "actual_calls": 0, "need_more_used": False, "fallback_used": False,
        "provider": type(provider).__name__, "provider_model": getattr(provider, "model", None),
        "validation_failures": issues,
    }
    writer = _provider_writer(provider) if budget else None
    if budget and writer is None:
        issues.append("structured_provider_unavailable")
    best = []
    for attempt in range(budget):
        remaining = timeout - (time.perf_counter() - started)
        if writer is None or remaining <= 0:
            break
        allow_queries = attempt == 0 and budget > 1 and tool_executor is not None
        messages, schema = _messages(packet, sources, allowed, skill, methods, allow_queries)
        try:
            audit["actual_calls"] += 1
            raw = writer(messages, schema, remaining)
            if isinstance(raw, tuple):
                raw, record = raw
                if isinstance(record, Mapping) and record.get("chat_attempted") is False:
                    audit["actual_calls"] -= 1
        except Exception as error:
            issues.append("provider_error:" + type(error).__name__)
            break
        statements, queries = _parse(raw, allowed, sources, scope, issues)
        if statements:
            best = statements
        if not (allow_queries and queries):
            break

        added = 0
        for query in queries:
            if time.perf_counter() - started >= timeout:
                break
            audit["need_more_used"] = True
            try:
                rows = tool_executor(query.tool, query.args)
                added += _admit_sources(sources, rows, reserved)
            except Exception as error:
                issues.append("read_error:" + type(error).__name__)
        if not added:
            break

    audit["fallback_used"] = bool(issues) or not best
    if not best:
        best = _fact_excerpts(sources, scope)
    audit["elapsed_seconds"] = time.perf_counter() - started
    return StatementDraft(statements=best, need_more=[]), audit
