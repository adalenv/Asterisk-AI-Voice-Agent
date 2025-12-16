#!/usr/bin/env python3
"""Simple MCP weather server using Open-Meteo API."""
import sys
import json
import urllib.request
import urllib.parse
import re

def send_response(req_id, result):
    msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
    body = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
    sys.stdout.flush()

def normalize_city(city):
    """Extract just the city name, stripping state/country."""
    # Remove common patterns like "City, State" or "City, Country"
    parts = re.split(r',\s*', city.strip())
    return parts[0].strip() if parts else city.strip()

def geocode(city):
    # Normalize city name to just the city part
    city_only = normalize_city(city)
    encoded_city = urllib.parse.quote(city_only, safe="")
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_city}&count=1"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    if data.get("results"):
        r = data["results"][0]
        return r["latitude"], r["longitude"], r.get("name", city_only)
    return None, None, city

def get_weather(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    current = data.get("current", {})
    temp = current.get("temperature_2m", "unknown")
    code = current.get("weather_code", 0)
    desc = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 
            45: "foggy", 51: "light drizzle", 61: "light rain", 71: "light snow",
            95: "thunderstorm"}.get(code, "variable")
    return temp, desc

def main():
    stdin = sys.stdin.buffer
    buffer = b""
    while True:
        chunk = stdin.read(1)
        if not chunk:
            break
        buffer += chunk
        if b"Content-Length:" in buffer and b"\r\n\r\n" in buffer:
            try:
                header_end = buffer.index(b"\r\n\r\n")
                header = buffer[:header_end].decode("ascii")
                content_length = None
                for h in header.split("\r\n"):
                    if h.lower().startswith("content-length:"):
                        content_length = int(h.split(":")[1].strip())
                        break
                if content_length is None:
                    buffer = b""
                    continue
                body_start = header_end + 4
                if len(buffer) >= body_start + content_length:
                    body = buffer[body_start:body_start + content_length].decode("utf-8")
                    buffer = buffer[body_start + content_length:]
                    msg = json.loads(body)
                    method = msg.get("method", "")
                    req_id = msg.get("id")
                    params = msg.get("params", {})
                    
                    if method == "initialize":
                        send_response(req_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "weather", "version": "1.0.0"}})
                    elif method == "tools/list":
                        send_response(req_id, {"tools": [{"name": "get_weather_by_city", "description": "Get current weather for a city", "inputSchema": {"type": "object", "properties": {"city": {"type": "string", "description": "City name"}}, "required": ["city"]}}]})
                    elif method == "tools/call":
                        tool_name = params.get("name", "")
                        args = params.get("arguments", {})
                        if tool_name == "get_weather_by_city":
                            city = args.get("city", "")
                            try:
                                lat, lon, name = geocode(city)
                                if lat is None:
                                    spoken = f"I could not find weather data for {city}."
                                else:
                                    temp, desc = get_weather(lat, lon)
                                    spoken = f"The weather in {name} is {desc} with a temperature of {temp} degrees Celsius."
                                send_response(req_id, {"content": [{"type": "text", "text": spoken}], "structured": {"spoken": spoken}})
                            except Exception as e:
                                send_response(req_id, {"content": [{"type": "text", "text": f"Weather lookup failed: {e}"}], "structured": {"spoken": f"Sorry, I could not get the weather. {e}"}})
                        else:
                            send_response(req_id, {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]})
                    elif method == "notifications/initialized":
                        pass
                    else:
                        if req_id is not None:
                            send_response(req_id, {})
            except Exception as e:
                sys.stderr.write(f"Error: {e}\n")
                sys.stderr.flush()
                buffer = b""

if __name__ == "__main__":
    main()
