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

# 원격 MCP 서버 기동 (streamable-http, 엔드포인트: /mcp)
CMD ["python", "server.py"]
