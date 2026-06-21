"""
Health_Navigator - MCP Server  [v4 범용 헬스 가이드형]

질환 발생 시 인턴 초진 양식을 결합해 예상 질환과 담당 진료과를 안내하고,
근처 병·의원을 찾아주며, 병원 방문 전 시행할 수 있는 일반적 자가 처치를
질병관리청 국가건강정보포털과 연계(딥링크)해 제공하는 Agent.

워크플로우 (단계별 툴):
  1) structure_intake        : 초진 기록을 표준 양식으로 정리
  2) predict_conditions      : 예상 질환(감별) + 담당 진료과 + 권장 진료 시급성
  3) find_nearby_hospital    : 지역·진료과 기준 근처 병·의원 조회(공공데이터)
  4) self_care_guidance      : 병원 방문 전 일반 자가 처치 + 국가건강정보포털 공식 링크

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
) -> str:
    """Health_Navigator: 인턴의 초진 기록(주호소·발병·증상·과거력 등)을 받아
    표준 초진 양식(SOAP 기반)으로 정리해 반환합니다. 이후 predict_conditions /
    find_nearby_hospital / self_care_guidance 툴의 입력으로 사용할 수 있습니다.
    진단이 아닌 기록 정리 단계입니다.

    Args:
        chief_complaint: 주호소 (예: "이틀 전부터 인후통과 기침")
        onset: 발병 양상/시각 (예: "2일 전부터 점점 심해짐")
        symptoms: 동반 증상 (예: "콧물, 미열, 가래")
        history: 과거력/현병력 (예: "알레르기 비염")
        medications: 복용 약물
        allergies: 알레르기 (예: "페니실린")
        vital_signs: 활력징후 (예: "BT 37.8, BP 120/80")
    """
    lines = ["【초진 기록 (정리본)】", "", "■ S (주관적):", f"  - 주호소(C.C.): {chief_complaint}"]
    if onset:
        lines.append(f"  - 발병/현병력(HPI): {onset}")
    if symptoms:
        lines.append(f"  - 동반 증상: {symptoms}")
    if history:
        lines.append(f"  - 과거력: {history}")
    if medications:
        lines.append(f"  - 복용 약물: {medications}")
    lines.append(f"  - 알레르기: {allergies or '특이사항 없음/미상'}")
    lines += ["", "■ O (객관적):", f"  - 활력징후: {vital_signs or '미측정'}", "",
              "■ A/P (사정·계획):",
              "  - 다음 단계: predict_conditions 로 예상 질환·담당 진료과 확인 →",
              "    find_nearby_hospital 로 근처 병원, self_care_guidance 로 방문 전 처치 안내",
              "", SAFETY]
    return "\n".join(lines)


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
) -> str:
    """Health_Navigator: 초진 소견(주호소·증상·과거력)을 내장 지식베이스와 대조해
    예상되는 질환 후보와 각 질환의 담당 진료과, 권장 진료 시급성을 안내합니다.
    확정 진단이 아니며, 근처 병원은 find_nearby_hospital, 방문 전 처치는
    self_care_guidance 툴로 이어서 확인하세요.

    Args:
        chief_complaint: 주호소
        symptoms: 동반 증상
        history: 과거력
    """
    combined = " ".join([chief_complaint, symptoms, history])
    emergency = kb.detect_emergency(combined)
    matched = kb.match_conditions(combined)

    out = ["【예상 질환 및 담당 진료과 (참고용)】", ""]
    if emergency:
        out += [kb.EMERGENCY_GUIDANCE, ""]

    if not matched:
        out += [
            "- 입력 소견에서 특정 질환 패턴을 식별하지 못했습니다.",
            "- 증상을 더 구체적으로 입력하거나, 일반적으로는 가정의학과/내과에서 초기 진료가 가능합니다.",
            "", SAFETY,
        ]
        return "\n".join(out)

    out.append("■ 예상 질환 후보 (관련도 높은 순):")
    primary_depts = []
    for cid, hits in matched[:5]:
        c = kb.CONDITIONS[cid]
        depts = c["department"] + (f" (또는 {', '.join(c['alt_depts'])})" if c["alt_depts"] else "")
        out.append(f"  • {c['name']}")
        out.append(f"     - 담당 진료과: {depts}")
        out.append(f"     - 권장: {kb.URGENCY_LABEL[c['urgency']]}")
        out.append(f"     - 뒷받침 소견: {', '.join(c['supporting'][:4])}")
        if c.get("extra_note"):
            out.append(f"     - 참고: {c['extra_note']}")
        if c["department"] not in primary_depts:
            primary_depts.append(c["department"])

    out += ["",
            f"■ 추천 진료과: {', '.join(primary_depts)}",
            "→ find_nearby_hospital(지역, 진료과)로 근처 병원을, "
            "self_care_guidance(질환명)로 방문 전 처치를 확인하세요.",
            "", SAFETY]
    return "\n".join(out)


# ===========================================================================
# 툴 3) 근처 병·의원 찾기 (공공데이터포털 - 병·의원 찾기 서비스)
# ===========================================================================
async def _fetch_xml(url: str, params: dict):
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return ET.fromstring(resp.text)


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

    Args:
        sido: 시도 (예: "서울특별시")
        sigungu: 시군구 (예: "강남구")
        department: 진료과 (예: "내과", "이비인후과", "정형외과" 등; 생략 가능)
    """
    if not SERVICE_KEY:
        return ("  (병원 조회 불가: 서비스 인증키 미설정 — 환경변수 DATA_GO_KR_SERVICE_KEY "
                "설정 필요)\n\n" + SAFETY)

    params = {"serviceKey": SERVICE_KEY, "Q0": sido, "Q1": sigungu,
              "pageNo": "1", "numOfRows": "10"}
    dept_code = kb.DEPARTMENT_CODES.get(department.strip()) if department else None
    if dept_code:
        params["QD"] = dept_code

    try:
        root = await _fetch_xml(f"{HOSP_BASE}/getHsptlMdcncListInfoInqire", params)
    except Exception as e:
        return f"  (병원 조회 오류: {e})\n\n{SAFETY}"

    code = root.findtext(".//resultCode")
    if code and code != "00":
        msg = root.findtext(".//resultMsg") or ""
        return f"  (병원 API 오류 코드 {code} {msg})\n\n{SAFETY}"

    items = root.findall(".//item")
    dept_label = f" · {department}" if dept_code else ""
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
        out.append(f"\n  (진료과 필터: {department})")
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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
