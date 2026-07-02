"""
Health_Navigator - MCP Server  [v4 범용 헬스 가이드형]

질환 발생 시 인턴 초진 양식을 결합해 예상 질환과 담당 진료과를 안내하고,
근처 병·의원을 찾아주며, 병원 방문 전 시행할 수 있는 일반적 자가 처치를
질병관리청 국가건강정보포털과 연계(딥링크)해 제공하는 Agent.

워크플로우 (단계별 툴):
  0) import_health_record    : (선택) 사용자가 내보낸 진료·투약 기록 → 개인 프로파일
  1) structure_intake        : 초진 기록을 표준 양식으로 정리
  2) predict_conditions      : 예상 질환(감별) + 담당 진료과 + 시급성 (profile로 개인화)
  3) find_nearby_hospital    : 지역·진료과 기준 근처 병·의원 조회(공공데이터)
  4) self_care_guidance      : 병원 방문 전 일반 자가 처치 + 국가건강정보포털 공식 링크
  5) generate_clinician_handoff : 진료 시 의사에게 보여줄 사전 초진 요약(인계용) 생성

[중요] 일반적인 건강정보 안내용 참고 도구이며, 의학적 확정 진단이 아닙니다.
[보안] 외부 API 인증키는 코드에 하드코딩하지 말고 환경변수로 주입하세요.
"""

import os
import httpx
import urllib.parse
import xml.etree.ElementTree as ET
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import kb

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
# 공공데이터포털 인증키 (Decoding 키). 보안상 환경변수로만 주입하세요.
SERVICE_KEY = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")

# 국립중앙의료원_전국 병·의원 찾기 서비스 (응급실/AED와 동일 제공기관 B552657 → 같은 키 사용)
HOSP_BASE = "http://apis.data.go.kr/B552657/HsptlAsembySearchService"

# 질병관리청 국가건강정보포털 (LINK형, 출처표시·비영리·변경금지) — 딥링크 출처
KDCA_PORTAL = "https://health.kdca.go.kr"

PORT = int(os.environ.get("PORT", "8000"))
mcp = FastMCP("Health_Navigator", host="0.0.0.0", port=PORT)

SAFETY = kb.SAFETY


def _kdca_link(term: str) -> str:
    """국가건강정보포털 건강정보 검색 딥링크(출처표시용)."""
    q = urllib.parse.quote(term)
    return f"{KDCA_PORTAL}/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?searchValue={q}"


# ===========================================================================
# 툴 0) 개인 건강기록 불러오기 (파이프라인 진입점)
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="개인 건강기록 불러오기 (진료·투약 내역 반영)",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    )
)
def import_health_record(record_text: str) -> str:
    """Health_Navigator: 사용자가 '나의건강기록' 앱이나 정부24 등에서 직접 내보낸
    본인의 진료·투약·건강검진 기록(텍스트/PDF에서 추출한 내용)을 받아, 복용약
    계열·기저질환·주요 검사수치·알레르기를 추출한 '개인 건강 프로파일'을 만듭니다.

    반환값의 [PROFILE] 한 줄 요약을 그대로 predict_conditions(profile=...) 와
    generate_clinician_handoff(profile=...) 의 인자로 넘기면, 같은 증상이라도 이
    사람의 약·기저질환을 반영해 시급성과 감별을 개인화합니다. 이것이 일반 증상
    안내(예: 응급똑똑)와 구분되는 핵심 단계입니다.

    [개인정보] 이 데이터는 서버에 저장되지 않으며 현재 세션에서만 사용됩니다.
    사용자가 스스로 내보낸 본인 기록만 입력하세요.

    Args:
        record_text: 나의건강기록/정부24 등에서 내보낸 진료·투약·검진 기록 원문
                     (예: "복용약: 와파린 5mg, 아스피린 100mg / 진단: 심방세동, 고혈압 ...")
    """
    flags = kb.parse_health_record(record_text)
    summary = kb.format_profile(flags)

    out = ["【개인 건강 프로파일 (요약)】", ""]
    out.append("■ 기저질환/과거력: " +
               (", ".join(flags["conditions"]) if flags["conditions"] else "추출 안 됨"))
    out.append("■ 복용약 계열: " +
               (", ".join(flags["medications"]) if flags["medications"] else "추출 안 됨"))
    if flags["labs"]:
        out.append("■ 주요 검사수치: " + ", ".join(flags["labs"]))
    if flags["allergies"]:
        out.append("■ 알레르기: " + "; ".join(flags["allergies"]))
    if flags["raw_med_lines"]:
        out.append("")
        out.append("■ 인식된 약물 관련 기록:")
        out += [f"  - {ln}" for ln in flags["raw_med_lines"]]

    out += ["",
            "■ [PROFILE] (다음 툴에 그대로 전달하세요):",
            f"  {summary}",
            "",
            "→ predict_conditions(주호소, 증상, 과거력, profile=위 [PROFILE]) 로 "
            "개인화된 예상 질환·시급성을 확인하고, generate_clinician_handoff 로 "
            "진료 시 보여줄 초진 요약을 만들 수 있어요.",
            "",
            "ⓘ 입력하신 건강기록은 저장되지 않고 이 세션에서만 사용됩니다.",
            "", SAFETY]
    return "\n".join(out)


# ===========================================================================
# 툴 1) 초진 양식 정리
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="초진 기록 표준 양식 정리",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    )
)
def structure_intake(
    chief_complaint: str,
    onset: str = "",
    symptoms: str = "",
    history: str = "",
    medications: str = "",
    allergies: str = "",
    vital_signs: str = "",
    ros: str = "",
    physical_exam: str = "",
    assessment: str = "",
    plan: str = "",
) -> str:
    """Health_Navigator: 인턴의 ER 초진 기록을 받아 표준 초진일지 양식
    (SOAP 기반 + ROS / PE / Assessment(r/o) / Plan)으로 정리해 반환합니다.
    이후 predict_conditions / find_nearby_hospital / self_care_guidance 툴의
    입력으로 사용할 수 있습니다. 진단이 아닌 기록 정리 단계입니다.

    S(주관적)는 C.C./HPI(PI)/동반증상/ROS/과거력(PHX)/약물/알레르기,
    O(객관적)는 활력징후/신체검진(PE), A는 사정(r/o 감별), P는 계획(w/u·tx)으로
    배치합니다. 비어 있는 항목은 출력에서 생략됩니다.

    [환자 문진 모드 — 호출 전 반드시 따를 것]
    환자가 증상 한두 마디만 보냈을 때, 비어 있는 항목을 추측하거나 임의로
    채우지 말고 환자에게 먼저 질문해 채운 뒤 이 툴을 호출하세요. 질문은 환자가
    부담스럽지 않게 한 번에 1~2개씩 자연스러운 말로 묻습니다.
      1) HPI(onset): 언제부터/얼마나 자주/갑자기 vs 서서히/악화·완화 요인.
         주호소의 성상(예: 변 색·양·횟수, 통증 부위·강도·방사 등).
      2) 동반 증상 / ROS: 주호소와 연관된 위험 증상 위주로 확인.
         예) 까만 변·혈변 → 어지러움·실신·식은땀, 토혈, 복통, 체중감소,
             평소 빈혈·소화불량, NSAID/항응고제 복용, 최근 음주.
         예) 흉통 → 호흡곤란·식은땀·방사통·실신, 운동 시 악화.
         예) 발열 → 오한, 기침·가래, 배뇨증상, 복통, 최근 항암/수술.
      3) PHX(history): 기저질환, 수술·시술력, 최근 입원, 추적 중인 과.
      4) medications / allergies: 복용 약(특히 항응고·항혈소판·NSAID·인슐린),
         약물 알레르기.
      5) vital_signs: 측정값이 있으면 입력(없으면 '미측정').
    위험징후(다량 출혈, 실신, 의식저하, 심한 흉통/호흡곤란 등)가 있으면
    문진을 끌지 말고 즉시 응급실 방문을 안내하세요.

    Args:
        chief_complaint: 주호소 C.C. (예: "melena")
        onset: 발병 양상/시각 / 현병력 PI (예: "내원 당일 7A30·9A melena 2회")
        symptoms: 동반 증상 (예: "general weakness(+), dyspnea(-)")
        history: 과거력 PHX / 현병력 배경 (예: "Pancreas head ca s/p Whipple's OP")
        medications: 복용 약물
        allergies: 알레르기 (예: "페니실린")
        vital_signs: 활력징후 (예: "BP 99/64, HR 72, RR 16, BT 36")
        ros: 계통 문진 ROS 체크 (예: "F/C(-/-) C/S/R(-/-/-) A/N/V/D/C(-/-/-/-/-)")
        physical_exam: 신체검진 PE 소견 (예: "Anemic conjunctiva, Abd Td(+) epigastric, DRE 흑색변")
        assessment: 사정/감별 진단 (예: "r/o marginal ulcer bleeding")
        plan: 계획 (예: "CBC f/u, consider EGD")
    """
    lines = ["【초진일지 (정리본)】", "", "■ S (주관적):", f"  - 주호소(C.C.): {chief_complaint}"]
    if onset:
        lines.append(f"  - 현병력(PI/HPI): {onset}")
    if symptoms:
        lines.append(f"  - 동반 증상: {symptoms}")
    if ros:
        lines.append(f"  - 계통 문진(ROS): {ros}")
    if history:
        lines.append(f"  - 과거력(PHX): {history}")
    if medications:
        lines.append(f"  - 복용 약물: {medications}")
    lines.append(f"  - 알레르기: {allergies or '특이사항 없음/미상'}")

    lines += ["", "■ O (객관적):", f"  - 활력징후(V/S): {vital_signs or '미측정'}"]
    if physical_exam:
        lines.append(f"  - 신체검진(PE): {physical_exam}")

    lines += ["", "■ A (사정):"]
    if assessment:
        lines.append(f"  - {assessment}")
    else:
        lines.append("  - (감별 미입력 — predict_conditions 로 예상 질환·진료과 확인 가능)")

    lines += ["", "■ P (계획):"]
    if plan:
        lines.append(f"  - {plan}")
    else:
        lines.append("  - (계획 미입력)")
    lines.append("  - 다음 단계(선택): predict_conditions → find_nearby_hospital / "
                 "self_care_guidance")

    lines += ["", SAFETY]
    return "\n".join(lines)


# ===========================================================================
# 툴 1-b) 주호소별 문진 질문 안내 (structure_intake 호출 전 단계)
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="주호소별 문진 질문 안내",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    )
)
def intake_questions(complaint: str) -> str:
    """Health_Navigator: 환자가 증상(주호소)을 말하면, 그 주호소에 맞춰 환자에게
    물어봐야 할 문진 질문 목록을 돌려줍니다. structure_intake로 기록을 정리하기
    '전' 단계에서 사용하세요. 반환된 질문을 환자에게 한 번에 1~2개씩 자연스럽게
    물어 답을 모은 뒤 structure_intake의 각 슬롯(onset/symptoms/ros/history 등)에
    채워 넣으면 됩니다. 진단이 아니라 '무엇을 물어볼지' 안내입니다.

    위험징후(red_flags)가 환자 답변에서 확인되면 문진을 계속 끌지 말고 즉시
    응급실 방문/119를 안내하세요.

    Args:
        complaint: 환자의 주호소 발화 (예: "까만 변을 봐요", "가슴이 터질 것 같아요")
    """
    text = complaint.strip()

    # 위급 신호 우선
    emergency = kb.detect_emergency(text)

    matched = kb.get_intake_questions(text)
    if not matched:
        out = ["【문진 질문 안내】", "",
               f"- '{complaint}'에 대한 사전 정의된 문진 세트를 찾지 못했습니다.",
               "- 일반 원칙으로 다음을 물어보세요:",
               "  • 발병 시점/양상(언제부터, 갑자기/서서히, 호전·악화)",
               "  • 증상의 성상(부위·강도·지속시간·동반 증상)",
               "  • 관련 위험 증상(발열·출혈·호흡곤란·의식변화 등)",
               "  • 과거력·수술력·복용 약물·알레르기",
               "  • 활력징후(측정 가능하면)"]
        if emergency:
            out += ["", kb.EMERGENCY_GUIDANCE]
        out += ["", SAFETY]
        return "\n".join(out)

    qid, q = matched[0]
    out = [f"【문진 질문 안내 — {q['name']}】", ""]
    if emergency:
        out += [kb.EMERGENCY_GUIDANCE, ""]

    out.append("■ 발병·성상 (HPI):")
    out += [f"  • {x}" for x in q["hpi"]]
    out.append("")
    out.append("■ 동반 증상·계통 문진 (ROS):")
    out += [f"  • {x}" for x in q["associated"]]
    out.append("")
    out.append("■ 과거력·약물·알레르기 (PHX):")
    out += [f"  • {x}" for x in q["phx_meds"]]
    out.append("")
    out.append(f"■ 진찰 초점(의료진용): {', '.join(q['exam_focus'])}")
    out.append("")
    out.append(f"⚠️ 위험징후(있으면 즉시 응급실 안내): {', '.join(q['red_flags'])}")

    if len(matched) > 1:
        others = ", ".join(kb.INTAKE_QUESTIONS[o]["name"] for o, _ in matched[1:3])
        out.append("")
        out.append(f"(추가로 고려할 주호소: {others})")

    out += ["",
            "→ 위 질문으로 답을 모은 뒤 structure_intake 로 기록을 정리하고, "
            "predict_conditions 로 예상 질환·진료과를 확인하세요.",
            "", SAFETY]
    return "\n".join(out)


# ===========================================================================
# 툴 2) 예상 질환 + 담당 진료과
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="예상 질환 및 담당 진료과 안내",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    )
)
def predict_conditions(
    chief_complaint: str,
    symptoms: str = "",
    history: str = "",
    profile: str = "",
) -> str:
    """Health_Navigator: 초진 소견(주호소·증상·과거력)을 내장 지식베이스와 대조해
    예상되는 질환 후보와 각 질환의 담당 진료과, 권장 진료 시급성을 안내합니다.
    확정 진단이 아니며, 근처 병원은 find_nearby_hospital, 방문 전 처치는
    self_care_guidance 툴로 이어서 확인하세요.

    profile(개인 건강 프로파일)을 함께 넘기면 이 사람의 복용약·기저질환을 반영해
    같은 증상이라도 시급성과 감별 순서를 개인화합니다(예: 항응고제 복용 중 흑색변
    → 위장관 출혈 위험 상향, 심혈관 위험인자 보유자 흉통 → 협심증 우선 배제).
    import_health_record 가 만들어 준 [PROFILE] 요약을 그대로 넣으면 됩니다.

    Args:
        chief_complaint: 주호소
        symptoms: 동반 증상
        history: 과거력
        profile: 개인 건강 프로파일(복용약·기저질환 요약; import_health_record 결과)
    """
    combined = " ".join([chief_complaint, symptoms, history])
    emergency = kb.detect_emergency(combined)
    matched = kb.match_conditions(combined)

    # --- 개인 건강기록 반영: 프로파일이 있으면 위험 가중 계산 ---
    flags = kb.parse_health_record(profile) if profile and profile.strip() else None
    # 프로파일 매칭에 증상 텍스트를 함께 넘겨 기저질환×증상 규칙을 평가
    risk = kb.assess_risk(flags, combined) if flags else {"notes": [], "boosted": {}, "added": []}

    def urgency_of(cid):
        return risk["boosted"].get(cid, kb.CONDITIONS[cid]["urgency"])

    # --- 문진 요약: 환자가 말한 내용을 먼저 되짚어 줌 ---
    out = ["【문진 요약】"]
    out.append(f"  - 말씀하신 주요 증상: {chief_complaint}")
    if symptoms:
        out.append(f"  - 함께 있는 증상: {symptoms}")
    if history:
        out.append(f"  - 과거력/복용약: {history}")
    if flags:
        out.append(f"  - 개인 건강기록: {profile.strip()}")
    out.append("")

    # --- 개인 이력 반영 주의(위험 가중이 발동한 경우 최우선 안내) ---
    if risk["notes"]:
        out += ["【개인 건강기록 반영 — 주의】"]
        out += [f"  ⚠️ {n}" for n in risk["notes"]]
        out.append("")

    out += ["【예상 질환 및 담당 진료과 (참고용)】", ""]
    if emergency:
        out += [kb.EMERGENCY_GUIDANCE, ""]

    # 재배열: (1) 매칭됐고 위험 가중된 질환 → (2) 그 외 매칭 질환 →
    #   (3) 매칭 안 됐지만 위험 규칙이 추가한 질환. 주호소와 직접 맞는 후보가
    #   부수적 주의 후보보다 아래로 밀리지 않도록 함.
    matched_ids = [cid for cid, _ in matched]
    boosted_in_matched = [c for c in matched_ids if c in risk["boosted"]]
    rest_matched = [c for c in matched_ids if c not in risk["boosted"]]
    added = [c for c in risk["added"] if c not in matched_ids]
    ordered = boosted_in_matched + rest_matched + added

    if not ordered:
        out += [
            "- 말씀하신 내용만으로는 특정 질환 패턴을 콕 집기 어려웠어요.",
            "- 증상을 조금 더 자세히 알려주시거나, 일반적으로는 가정의학과/내과에서 초기 진료가 가능합니다.",
            "", SAFETY,
        ]
        return "\n".join(out)

    # 가장 관련도 높은(또는 위험 가중된) 후보를 한 줄 결론으로 먼저 제시
    top_id = ordered[0]
    top = kb.CONDITIONS[top_id]
    top_dept = top["department"] + (f"(또는 {', '.join(top['alt_depts'])})" if top["alt_depts"] else "")
    boosted_tag = " (개인 이력 반영해 시급성 상향)" if top_id in risk["boosted"] else ""
    out.append(f"■ 가장 주의해서 볼 방향: '{top['name']}'이(가) 의심되어 "
               f"{top_dept} 진료를 권합니다.{boosted_tag}")
    out.append(f"  - 권장 시급성: {kb.URGENCY_LABEL[urgency_of(top_id)]}")
    out.append(f"  - 그렇게 본 이유(뒷받침 소견): {', '.join(top['supporting'][:4])}")
    if top.get("extra_note"):
        out.append(f"  - 참고: {top['extra_note']}")
    out.append("")

    out.append("■ 그 외 함께 고려할 수 있는 후보:")
    primary_depts = [top["department"]]
    others = ordered[1:5]
    if others:
        for cid in others:
            c = kb.CONDITIONS[cid]
            depts = c["department"] + (f" (또는 {', '.join(c['alt_depts'])})" if c["alt_depts"] else "")
            tag = " ★개인 이력 반영" if cid in risk["boosted"] else ""
            out.append(f"  • {c['name']} — {depts} / {kb.URGENCY_LABEL[urgency_of(cid)]}{tag}")
            if c["department"] not in primary_depts:
                primary_depts.append(c["department"])
    else:
        out.append("  • (추가 후보 없음)")

    out += ["",
            f"■ 추천 진료과: {', '.join(primary_depts)}",
            "→ find_nearby_hospital(지역, 진료과)로 근처 병원을, "
            "self_care_guidance(질환명)로 방문 전 처치를, "
            "generate_clinician_handoff 로 진료 시 보여줄 초진 요약을 만드세요.",
            "", SAFETY]
    return "\n".join(out)


# ===========================================================================
# 툴 3) 근처 병·의원 찾기 (공공데이터포털 - 병·의원 찾기 서비스)
# ===========================================================================
async def _fetch_text(url: str, params: dict, attempts: int = 3):
    """요청을 보내고 (status_code, 응답본문)을 반환. 상태코드 검사는 호출부에서.
    공공 API 지연 대비: 타임아웃을 넉넉히 두고, 타임아웃/네트워크 오류 시 재시도."""
    timeout = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
    last_exc = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for i in range(attempts):
            try:
                resp = await client.get(url, params=params)
                return resp.status_code, resp.text
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e  # 일시적 지연/네트워크 오류 → 재시도
                continue
    raise last_exc


@mcp.tool(
    annotations=ToolAnnotations(
        title="근처 병·의원 찾기 (지역·진료과)",
        readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True,
    )
)
async def find_nearby_hospital(
    sido: str,
    sigungu: str,
    department: str = "",
) -> str:
    """Health_Navigator: 지정한 지역(시도/시군구)에서 진료과 기준으로 근처 병·의원을
    국립중앙의료원 공공데이터(병·의원 찾기 서비스)로 조회합니다. department를 지정하면
    해당 진료과로 필터링합니다. 실시간 공공데이터이므로 호출 시점에 따라 결과가 달라질
    수 있습니다.

    department에는 큰 단위 진료과명을 권장합니다(내과·외과·정형외과·이비인후과 등).
    소화기내과·심장내과 같은 세부 분과나 '정신과'·'비뇨기과' 같은 구명칭을 넣으면
    상위 표준 진료과로 자동 변환됩니다. 변환할 수 없는 이름이면 지원 진료과 목록을
    안내하며, 진료과 없이 호출하면 지역 내 병·의원 전체를 조회합니다.

    Args:
        sido: 시도 (예: "서울특별시")
        sigungu: 시군구 (예: "강남구")
        department: 진료과 (예: "내과", "소화기내과"→내과, "정신과"→정신건강의학과; 생략 가능)
    """
    if not SERVICE_KEY:
        return ("  (병원 조회 불가: 서비스 인증키 미설정 — 환경변수 DATA_GO_KR_SERVICE_KEY "
                "설정 필요)\n\n" + SAFETY)

    params = {"serviceKey": SERVICE_KEY, "Q0": sido, "Q1": sigungu,
              "pageNo": "1", "numOfRows": "10"}

    # 진료과 정규화: 세부 분과(소화기내과 등)는 상위 과로, 매핑 불가 시 안내 후 종료
    dept_canon = None
    dept_code = None
    if department and department.strip():
        resolved = kb.resolve_department(department)
        if not resolved:
            supported = ", ".join(kb.SUPPORTED_DEPARTMENTS)
            return (f"  ('{department}'은(는) 이 검색에서 지원하지 않는 진료과명이에요.)\n"
                    f"  지원 진료과: {supported}\n"
                    f"  (세부 분과는 상위 과로 입력하세요. 예: 소화기내과·심장내과 → 내과)\n"
                    f"  진료과 없이도 지역 병·의원 전체를 조회할 수 있습니다.\n\n{SAFETY}")
        dept_canon, dept_code = resolved
        params["QD"] = dept_code

    try:
        status, body = await _fetch_text(f"{HOSP_BASE}/getHsptlMdcncListInfoInqire", params)
    except Exception as e:
        # str(e)가 비어 있는 예외도 있어 type/repr까지 노출해 진단을 돕는다
        return f"  (병원 조회 연결 오류: {type(e).__name__}: {e!r})\n\n{SAFETY}"

    # HTTP 상태 비정상이면 본문 일부를 함께 보여 원인 파악을 돕는다
    if status != 200:
        snippet = (body or "").strip().replace("\n", " ")[:300]
        return f"  (병원 API HTTP {status} 응답)\n  본문: {snippet}\n\n{SAFETY}"

    # XML 파싱 시도 — 실패하면 본문 일부 노출(데이터포털은 인증오류를 본문에 담아줌)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        snippet = (body or "").strip().replace("\n", " ")[:300]
        return (f"  (병원 API 응답 파싱 실패: {e})\n  본문: {snippet}\n\n{SAFETY}")

    # 데이터포털 인증/오류 메시지(returnAuthMsg / returnReasonCode / resultMsg) 처리
    auth_msg = root.findtext(".//returnAuthMsg")
    reason = root.findtext(".//returnReasonCode")
    if auth_msg or (reason and reason not in ("00", "0")):
        return (f"  (병원 API 인증/요청 오류: {auth_msg or ''} "
                f"코드 {reason or '?'})\n"
                f"  → data.go.kr에서 해당 서비스 활용신청 승인·키 종류(Decoding) 확인 필요\n\n{SAFETY}")

    code = root.findtext(".//resultCode")
    if code and code != "00":
        msg = root.findtext(".//resultMsg") or ""
        return f"  (병원 API 오류 코드 {code} {msg})\n\n{SAFETY}"

    # 입력이 정규화됐으면(예: 소화기내과→내과) 라벨에 그 사실을 표시
    if dept_canon and department.strip() != dept_canon:
        dept_filter_label = f"{dept_canon}(입력: {department.strip()})"
    else:
        dept_filter_label = dept_canon or ""
    dept_label = f" · {dept_canon}" if dept_code else ""

    items = root.findall(".//item")
    if not items:
        return (f"  ('{sido} {sigungu}{dept_label}' 조건의 병·의원 조회 결과 없음)\n"
                f"  (진료과를 바꾸거나 지역을 넓혀 다시 시도하세요.)\n\n{SAFETY}")

    out = [f"【근처 병·의원 — {sido} {sigungu}{dept_label}】"]
    for it in items[:7]:
        name = it.findtext("dutyName") or it.findtext("dutyname") or "이름미상"
        addr = it.findtext("dutyAddr") or "-"
        tel = it.findtext("dutyTel1") or it.findtext("dutytel1") or "-"
        div = it.findtext("dutyDivNam") or ""
        div_label = f"[{div}] " if div else ""
        out.append(f"  • {div_label}{name}\n     {addr} | ☎ {tel}")
    if dept_code:
        out.append(f"\n  (진료과 필터: {dept_filter_label})")
    out += ["", SAFETY]
    return "\n".join(out)


# ===========================================================================
# 툴 4) 병원 방문 전 자가 처치 + 국가건강정보포털 연계
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="병원 방문 전 자가 처치 안내 (질병관리청 연계)",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
    )
)
def self_care_guidance(query: str) -> str:
    """Health_Navigator: 질환명 또는 증상을 입력하면, 병원 방문 전 시행할 수 있는
    일반적인 자가 처치 안내와 주의할 위험징후를 제공하고, 해당 질환의 질병관리청
    국가건강정보포털 공식 콘텐츠 링크(출처표시)를 함께 안내합니다. 처치 안내는 일반
    참고용이며, 공식 원문은 제공된 포털 링크에서 확인하세요.

    Args:
        query: 질환명(예: "감기") 또는 증상(예: "콧물 기침")
    """
    q = query.strip()
    if kb.detect_emergency(q):
        return kb.EMERGENCY_GUIDANCE + "\n\n" + SAFETY

    # 질환명 직접 매칭 → 없으면 증상 키워드로 확장
    targets = [cid for cid, c in kb.CONDITIONS.items()
               if c["name"] in q or q in c["name"] or q == cid]
    if not targets:
        targets = [cid for cid, _ in kb.match_conditions(q)]

    if not targets:
        return "\n".join([
            f"'{query}'에 해당하는 항목을 찾지 못했습니다.",
            "predict_conditions 로 먼저 예상 질환을 확인한 뒤 그 질환명으로 다시 시도하세요.",
            "",
            f"질병관리청 국가건강정보포털에서 직접 검색: {KDCA_PORTAL}",
            "", SAFETY,
        ])

    out = []
    for cid in targets[:3]:
        c = kb.CONDITIONS[cid]
        out.append(f"【{c['name']} — 병원 방문 전 자가 처치 (참고용)】")
        out.append(f"  담당 진료과: {c['department']}"
                   + (f" (또는 {', '.join(c['alt_depts'])})" if c["alt_depts"] else ""))
        out.append("")
        out.append("■ 자가 처치 안내:")
        out += [f"  • {a}" for a in c["care"]]
        out.append("")
        out.append(f"■ 즉시/빠른 진료가 필요한 위험징후: {', '.join(c['red_flags'])}")
        if c.get("extra_note"):
            out.append(f"■ 참고: {c['extra_note']}")
        out.append("")
        out.append(f"■ 출처(원문): 질병관리청 국가건강정보포털 — '{c['kdca_term']}'")
        out.append(f"  {_kdca_link(c['kdca_term'])}")
        out.append("")
    out.append("ⓘ 자가 처치 안내는 국가건강정보포털 공개정보를 참고한 일반 안내이며, "
               "공식 콘텐츠 원문은 위 포털 링크에서 확인하세요(출처표시·비영리·변경금지).")
    out.append("")
    out.append(SAFETY)
    return "\n".join(out)


# ===========================================================================
# 툴 5) 의료진 인계용 초진 요약 생성 (진료 전 문진표)
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="의료진 인계용 초진 요약 생성",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
    )
)
def generate_clinician_handoff(
    chief_complaint: str,
    onset: str = "",
    symptoms: str = "",
    ros: str = "",
    history: str = "",
    medications: str = "",
    allergies: str = "",
    vital_signs: str = "",
    profile: str = "",
    assessment: str = "",
    department: str = "",
    urgency: str = "",
    plan: str = "",
) -> str:
    """Health_Navigator: 지금까지 모은 문진 내용과 개인 건강기록(profile)을 합쳐,
    환자가 '진료 시 의사에게 그대로 보여줄 수 있는' 한 장짜리 사전 초진 요약(문진표)을
    만듭니다. 환자용 안내가 아니라 의료진 인계용 산출물이라는 점이 일반 증상 안내와
    구분되는 지점입니다. 환자 진술과 본인 제공 기록에 기반하며 확정 진단이 아닙니다.

    profile 에는 import_health_record 가 만든 [PROFILE] 요약을, assessment/department/
    urgency 에는 predict_conditions 결과를 넣으면 자동으로 반영됩니다.

    Args:
        chief_complaint: 주호소
        onset: 발병 양상/현병력(HPI)
        symptoms: 동반 증상
        ros: 계통 문진(ROS)
        history: 과거력(문진으로 확인한 내용)
        medications: 문진으로 확인한 복용약
        allergies: 알레르기
        vital_signs: 활력징후(측정된 경우)
        profile: 개인 건강 프로파일(import_health_record 결과 [PROFILE])
        assessment: 예상 질환/감별(predict_conditions 결과)
        department: 권장 진료과
        urgency: 권장 시급성
        plan: 계획/권고
    """
    flags = kb.parse_health_record(profile) if profile and profile.strip() else None

    # 문진으로 받은 값과 개인기록에서 추출한 값을 합쳐 보여줌(중복은 자연스럽게 병기)
    rec_conditions = ", ".join(flags["conditions"]) if flags and flags["conditions"] else ""
    rec_meds = ", ".join(flags["medications"]) if flags and flags["medications"] else ""
    rec_labs = ", ".join(flags["labs"]) if flags and flags["labs"] else ""
    rec_allergy = "; ".join(flags["allergies"]) if flags and flags["allergies"] else ""

    line = "─" * 44
    out = [line,
           "  사전 초진 요약 (환자 제공 · 의료진 인계용)",
           "  생성: Health_Navigator (참고용, 확정 진단 아님)",
           line, ""]

    out.append("■ 주호소 (C.C.)")
    out.append(f"  {chief_complaint}")
    if urgency:
        out.append(f"  권장 시급성: {urgency}")
    if department:
        out.append(f"  권장 진료과: {department}")
    out.append("")

    out.append("■ 현병력 (HPI) / 동반 증상")
    out.append(f"  - 발병·경과: {onset or '기재 없음'}")
    if symptoms:
        out.append(f"  - 동반 증상: {symptoms}")
    if ros:
        out.append(f"  - 계통 문진(ROS): {ros}")
    out.append("")

    out.append("■ 과거력·복용약 (환자 진술 + 본인 제공 건강기록)")
    hx = history or ""
    if rec_conditions:
        hx = (hx + f" / [기록] 기저질환: {rec_conditions}").strip(" /")
    out.append(f"  - 과거력: {hx or '기재 없음'}")
    md = medications or ""
    if rec_meds:
        md = (md + f" / [기록] 복용약 계열: {rec_meds}").strip(" /")
    out.append(f"  - 복용약: {md or '기재 없음'}")
    out.append(f"  - 알레르기: {allergies or rec_allergy or '특이사항 없음/미상'}")
    if rec_labs:
        out.append(f"  - 최근 검사수치(기록): {rec_labs}")
    out.append("")

    out.append("■ 활력징후 (V/S)")
    out.append(f"  {vital_signs or '미측정'}")
    out.append("")

    out.append("■ 사정 (A) — 예상 질환/감별 (참고)")
    out.append(f"  {assessment or '(predict_conditions 결과 입력)'}")
    out.append("")

    out.append("■ 계획/권고 (P)")
    out.append(f"  {plan or '의료진 판단에 따름'}")
    out.append("")

    out.append(line)
    out.append("ⓘ 본 요약은 환자 진술과 환자 본인이 제공한 건강기록에 기반한 참고 "
               "자료이며, 의학적 확정 진단이 아닙니다. 최종 판단은 진료 의료진에 따릅니다.")
    out.append("ⓘ 개인 건강기록은 이 요약 생성에만 사용되며 서버에 저장되지 않습니다.")
    out.append("")
    out.append(SAFETY)
    return "\n".join(out)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
