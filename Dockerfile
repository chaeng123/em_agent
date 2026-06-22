FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY server.py .
COPY kb.py .

# PlayMCP/카카오 클라우드가 주입하는 포트 (기본 8000)
ENV PORT=8000
EXPOSE 8000

# 공공데이터포털 병·의원 찾기 서비스 인증키 (Decoding serviceKey)
# [중요] 배포 UI에 환경변수 칸이 없을 때만 여기에 직접 입력합니다.
#        이 값이 들어가면 키가 git 저장소에 포함되므로 반드시 private 저장소로 두세요.
#        가능하면 배포 단계의 '환경 변수' 칸 사용을 우선하세요.
ENV DATA_GO_KR_SERVICE_KEY="QQD8UdIH+e1mcy2SVrOA93bfqnp5Z4C5S5j0gvsaWqlhd8fcp01fdSax+BGjA2j1+ocWuRkGXNzvluejd36dHg=="

# 원격 MCP 서버 기동 (streamable-http, 엔드포인트: /mcp)
CMD ["python", "server.py"]
