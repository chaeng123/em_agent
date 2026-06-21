"""
Emergency_Agent (EMAgent) - MCP Server  [v3 단계별 분리형]

인턴 초진 기록 기반 응급 의사결정 지원 Agent.

워크플로우 (단계별 툴):
  1) structure_intake          : 초진 기록을 표준 응급 초진 양식으로 정리
  2) predict_conditions        : 정리된 소견으로 예상 질환(감별진단) + KTAS 추정
  3) recommend_initial_actions : 예상 질환별 초기 검사·조치·위험징후를 KB 기반 추천
  4) find_emergency_resources  : 근처 응급실 실시간 가용병상 / AED 위치 조회(공공데이터)

[중요] 본 서버는 의료 정보 제공·응급실 연계를 위한 '참고용' 도구입니다.
실제 응급도 판정·검사·처치는 반드시 의료진의 판단을 따라야 합니다.

[보안] 외부 API 인증키는 코드에 하드코딩하지 말고, 반드시 환경변수
(DATA_GO_KR_SERVICE_KEY)로 주입하세요.
"""

import os
import httpx
import xml.etree.ElementTree as ET
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import kb

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
# 공공데이터포털 인증키 (Decoding 키). 보안상 환경변수로만 주입하세요.
SERVICE_KEY = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")

ERMCT_BASE = "http://apis.data.go.kr/B552657/ErmctInfoInqireService"   # 응급실
AED_BASE = "http://apis.data.go.kr/B552657/AEDInfoInqireService"       # AED

PORT = int(os.environ.get("PORT", "8000"))
mcp = FastMCP("Emergency_Agent", host="0.0.0.0", port=PORT)

SAFETY = ("⚠️ 본 정보는 참고용입니다. 실제 응급 상황에서는 즉시 119 신고 또는 "
          "가까운 응급실 방문이 우선이며, 최종 판정·처치는 의료진의 판단을 따릅니다.")

KTAS_LABEL = {
    1: "KTAS 1 (소생)", 2: "KTAS 2 (응급)", 3: "KTAS 3 (긴급)",
    4: "KTAS 4 (준응급)", 5: "KTAS 5 (비응급)",
}


# ===========================================================================
# 툴 1) 초진 양식 정리
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="초진 기록 표준 양식 정리",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
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
    """Emergency_Agent: 인턴의 초진 기록(주호소·발병·증상·과거력 등)을 받아
    응급실 표준 초진 양식(SOAP 기반)으로 깔끔하게 정리해 반환합니다. 이후
    predict_conditions / recommend_initial_actions 툴의 입력으로 그대로 사용할 수
    있습니다. 진단이 아닌 기록 정리 단계입니다.

    Args:
        chief_complaint: 주호소 (예: "갑작스러운 흉통")
        onset: 발병 양상/시각 (예: "1시간 전 안정 중 급성 발생")
        symptoms: 동반 증상 (예: "식은땀, 좌측 팔 저림, 오심")
        history: 과거력/현병력 (예: "고혈압, 당뇨, 흡연 20갑년")
        medications: 복용 약물 (예: "아스피린, 메트포르민")
        allergies: 알레르기 (예: "페니실린")
        vital_signs: 활력징후 (예: "BP 90/60, HR 110, RR 22, SpO2 92%, BT 36.8")
    """
    lines = [
        "【응급 초진 기록 (정리본)】",
        "",
        "■ S (주관적):",
        f"  - 주호소(C.C.): {chief_complaint}",
    ]
    if onset:
        lines.append(f"  - 발병/현병력(HPI): {onset}")
    if symptoms:
        lines.append(f"  - 동반 증상: {symptoms}")
    if history:
        lines.append(f"  - 과거력/위험인자: {history}")
    if medications:
        lines.append(f"  - 복용 약물: {medications}")
    lines.append(f"  - 알레르기: {allergies or '특이사항 없음/미상'}")
    lines.append("")
    lines.append("■ O (객관적):")
    lines.append(f"  - 활력징후: {vital_signs or '미측정 — 측정 필요'}")
    lines.append("")
    lines.append("■ A/P (사정·계획):")
    lines.append("  - 다음 단계: predict_conditions 로 예상 질환·KTAS 추정 →")
    lines.append("    recommend_initial_actions 로 질환별 초기 조치 확인")
    lines.append("")
    lines.append(SAFETY)
    return "\n".join(lines)


# ===========================================================================
# 툴 2) 예상 질환(감별진단) 추론
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="예상 질환(감별진단) 및 KTAS 추정",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def predict_conditions(
    chief_complaint: str,
    symptoms: str = "",
    history: str = "",
    vital_signs: str = "",
) -> str:
    """Emergency_Agent: 초진 소견(주호소·증상·과거력·활력징후)을 내장 지식베이스와
    대조해 예상되는 질환(감별진단) 후보와 참고용 KTAS 응급도를 추정합니다. 각 질환의
    뒷받침 소견도 함께 제시합니다. 확정 진단이 아니며, 구체적 조치는
    recommend_initial_actions 툴로 확인하세요.

    Args:
        chief_complaint: 주호소
        symptoms: 동반 증상
        history: 과거력/위험인자
        vital_signs: 활력징후
    """
    combined = " ".join([chief_complaint, symptoms, history, vital_signs])

    # 1) 심정지 우선 판정 (KTAS 1)
    if kb.detect_arrest(combined):
        g = kb.ARREST_GUIDANCE
        return "\n".join([
            "【예상 질환 추정 (참고용)】",
            f"- 추정 응급도: {KTAS_LABEL[1]}",
            f"- ⚠️ {g['name']} — 즉시 소생술 필요",
            "",
            "→ recommend_initial_actions('심정지') 또는 즉시 CPR/AED, 119 신고.",
            "",
            SAFETY,
        ])

    # 2) 증상 클러스터 매칭
    matched = kb.match_presentations(combined)
    if not matched:
        return "\n".join([
            "【예상 질환 추정 (참고용)】",
            "- 입력 소견에서 특정 응급 패턴을 식별하지 못했습니다.",
            "- 활력징후·증상을 더 구체적으로 입력하거나, 의료진의 직접 평가를 권합니다.",
            "- 자가 판단이 어려우면 응급의료상담 1339 문의.",
            "",
            SAFETY,
        ])

    # 3) 감별진단 집계 (중복 제거, KTAS 오름차순=중증 우선)
    seen, conditions = set(), []
    cluster_names = []
    min_ktas = 5
    for pres, hits in matched:
        cluster_names.append(f"{pres['name']}(일치: {', '.join(hits)})")
        min_ktas = min(min_ktas, pres["ktas_hint"])
        for cid in pres["conditions"]:
            if cid not in seen:
                seen.add(cid)
                conditions.append(kb.CONDITIONS[cid])

    conditions.sort(key=lambda c: c["ktas"])

    out = [
        "【예상 질환 추정 (참고용)】",
        f"- 식별된 주호소 패턴: {'; '.join(cluster_names)}",
        f"- 추정 응급도(주호소 기준): {KTAS_LABEL.get(min_ktas, min_ktas)}"
        " — 아래 감별진단 중 더 높은 등급이 있으면 그에 준해 대응",
        "",
        "■ 감별진단 후보 (중증도 높은 순):",
    ]
    for c in conditions:
        out.append(f"  • {c['name']}  [{KTAS_LABEL.get(c['ktas'], c['ktas'])}]")
        out.append(f"     - 뒷받침 소견: {', '.join(c['supporting'][:4])}")
    out.append("")
    out.append("→ 구체적 초기 검사·조치는 recommend_initial_actions 에 질환명 또는")
    out.append("   주호소를 입력해 확인하세요.")
    out.append("")
    out.append(SAFETY)
    return "\n".join(out)


# ===========================================================================
# 툴 3) 질환별 초기 조치 추천
# ===========================================================================
@mcp.tool(
    annotations=ToolAnnotations(
        title="예상 질환별 초기 검사·조치 추천",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def recommend_initial_actions(query: str) -> str:
    """Emergency_Agent: 질환명 또는 주호소/증상을 입력하면, 내장 지식베이스에서
    해당하는 예상 질환을 찾아 권장 초기 검사, 우선 조치(간호중재), 주의할 위험징후
    (red flag)를 참고용으로 제시합니다. 모든 처치·투약은 의료진 지시를 따릅니다.

    Args:
        query: 질환명(예: "급성 관상동맥증후군") 또는 증상/주호소(예: "흉통")
               또는 "심정지" 입력 시 즉시 소생 처치 안내
    """
    q = query.strip()

    # 심정지 즉시 처치
    if kb.detect_arrest(q) or "심정지" in q or "cpr" in q.lower():
        g = kb.ARREST_GUIDANCE
        body = [f"【{g['name']} — 초기 처치 (참고용, {KTAS_LABEL[1]})】", "", "■ 즉시 조치:"]
        body += [f"  • {a}" for a in g["initial_actions"]]
        body += ["", f"■ 위험징후: {', '.join(g['red_flags'])}", "", SAFETY]
        return "\n".join(body)

    # 1) 질환명 직접 매칭
    targets = [c for c in kb.CONDITIONS.values()
               if c["name"] in q or q in c["name"] or any(q in s for s in [c["name"]])]
    # 2) 질환명으로 못 찾으면 증상 클러스터로 확장
    if not targets:
        seen = set()
        for pres, _ in kb.match_presentations(q):
            for cid in pres["conditions"]:
                if cid not in seen:
                    seen.add(cid)
                    targets.append(kb.CONDITIONS[cid])

    if not targets:
        return "\n".join([
            f"'{query}'에 해당하는 질환을 지식베이스에서 찾지 못했습니다.",
            "predict_conditions 로 먼저 예상 질환을 확인한 뒤, 그 질환명으로 다시 시도하세요.",
            "",
            SAFETY,
        ])

    targets.sort(key=lambda c: c["ktas"])
    out = []
    for c in targets:
        out.append(f"【{c['name']} — 초기 대응 (참고용, {KTAS_LABEL.get(c['ktas'], c['ktas'])})】")
        out.append("")
        out.append("■ 권장 초기 검사:")
        out += [f"  • {w}" for w in c["initial_workup"]]
        out.append("")
        out.append("■ 우선 조치/간호중재:")
        out += [f"  • {a}" for a in c["initial_actions"]]
        out.append("")
        out.append(f"■ 위험징후(즉시 의료진 보고): {', '.join(c['red_flags'])}")
        out.append("")
    out.append(SAFETY)
    return "\n".join(out)


# ===========================================================================
# 툴 4) 응급실/AED 자원 조회 (공공데이터포털 API)
# ===========================================================================
async def _fetch_xml(url: str, params: dict):
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return ET.fromstring(resp.text)


async def _find_er(sido: str, sigungu: str) -> str:
    if not SERVICE_KEY:
        return "  (응급실 조회 불가: 서비스 인증키 미설정 — 환경변수 DATA_GO_KR_SERVICE_KEY 설정 필요)"
    params = {"serviceKey": SERVICE_KEY, "STAGE1": sido, "STAGE2": sigungu,
              "pageNo": "1", "numOfRows": "10"}
    try:
        root = await _fetch_xml(f"{ERMCT_BASE}/getEmrrmRltmUsefulSckbdInfoInqire", params)
    except Exception as e:
        return f"  (응급실 조회 오류: {e})"
    code = root.findtext(".//resultCode")
    if code and code != "00":
        return f"  (응급실 API 오류 코드 {code})"
    items = root.findall(".//item")
    if not items:
        return f"  ('{sido} {sigungu}' 응급실 조회 결과 없음)"
    out = []
    for it in items[:5]:
        name = it.findtext("dutyname") or "이름미상"
        tel = it.findtext("dutytel3") or "-"
        beds = it.findtext("hvec")
        bed = f"{beds}병상" if beds not in (None, "") else "정보없음"
        out.append(f"  • {name} | 응급실 가용 {bed} | ☎ {tel}")
    return "\n".join(out)


async def _find_aed(sido: str, sigungu: str) -> str:
    if not SERVICE_KEY:
        return "  (AED 조회 불가: 서비스 인증키 미설정 — 환경변수 DATA_GO_KR_SERVICE_KEY 설정 필요)"
    params = {"serviceKey": SERVICE_KEY, "STAGE1": sido, "STAGE2": sigungu,
              "pageNo": "1", "numOfRows": "10"}
    try:
        root = await _fetch_xml(f"{AED_BASE}/getEgytAedManageInfoInqire", params)
    except Exception as e:
        return f"  (AED 조회 오류: {e})"
    code = root.findtext(".//resultCode")
    if code and code != "00":
        return f"  (AED API 오류 코드 {code})"
    items = root.findall(".//item")
    if not items:
        return f"  ('{sido} {sigungu}' AED 조회 결과 없음)"
    out = []
    for it in items[:5]:
        org = it.findtext("org") or it.findtext("buildPlace") or "설치기관 미상"
        place = it.findtext("buildPlace") or it.findtext("clerkTel") or ""
        loc = f" ({place})" if place and place != org else ""
        out.append(f"  • {org}{loc}")
    return "\n".join(out)


@mcp.tool(
    annotations=ToolAnnotations(
        title="근처 응급실 실시간 병상 / AED 위치 조회",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,   # 실시간 가용병상은 호출 시점마다 결과가 달라짐
        openWorldHint=True,     # 공공데이터포털 외부 API 조회
    )
)
async def find_emergency_resources(
    sido: str,
    sigungu: str,
    include_aed: bool = False,
) -> str:
    """Emergency_Agent: 지정한 지역(시도/시군구)의 근처 응급실 실시간 가용병상 현황을
    공공데이터포털 API로 조회합니다. include_aed=True이면 근처 AED(자동심장충격기)
    위치도 함께 조회합니다(심정지 의심 시 유용). 실시간 데이터이므로 호출 시점에 따라
    결과가 달라질 수 있습니다.

    Args:
        sido: 시도 (예: "서울특별시")
        sigungu: 시군구 (예: "강남구")
        include_aed: True이면 근처 AED 위치도 함께 조회
    """
    parts = [f"【근처 응급실 실시간 현황 — {sido} {sigungu}】", await _find_er(sido, sigungu)]
    if include_aed:
        parts.append("")
        parts.append(f"【근처 AED(제세동기) 위치 — {sido} {sigungu}】")
        parts.append(await _find_aed(sido, sigungu))
    parts.append("")
    parts.append(SAFETY)
    return "\n".join(parts)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
