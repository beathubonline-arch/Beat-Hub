import json, urllib.parse, urllib.request
from datetime import datetime, timezone

UA="MkulimaAI/0.1 https://mkulima-ai-whatsapp.onrender.com"
GEOCODER="https://nominatim.openstreetmap.org/search"
FORECAST="https://api.met.no/weatherapi/locationforecast/2.0/compact"

def _json(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def resolve_kenya_location(name):
    q=(name or "").strip()
    if len(q)<2: return {"ok":False,"status":"missing_location"}
    params=urllib.parse.urlencode({"q":q+", Kenya","format":"jsonv2","limit":1,"countrycodes":"ke"})
    try:
        rows=_json(GEOCODER+"?"+params)
        if not rows: return {"ok":False,"status":"location_not_found","query":q}
        x=rows[0]
        return {"ok":True,"status":"resolved","query":q,"name":x.get("display_name",q),
                "lat":round(float(x["lat"]),4),"lon":round(float(x["lon"]),4),
                "provider":"OpenStreetMap Nominatim","license":"ODbL"}
    except Exception as exc:
        return {"ok":False,"status":"geocoder_error","reason":type(exc).__name__,"query":q}

def live_weather(location):
    geo=resolve_kenya_location(location)
    if not geo.get("ok"): return {"ok":False,"status":geo.get("status"),"location":geo}
    params=urllib.parse.urlencode({"lat":geo["lat"],"lon":geo["lon"]})
    try:
        data=_json(FORECAST+"?"+params)
        series=((data.get("properties") or {}).get("timeseries") or [])
        if not series: return {"ok":False,"status":"forecast_empty","location":geo}
        now=datetime.now(timezone.utc)
        future=[]
        for item in series:
            try:
                ts=datetime.fromisoformat(item["time"].replace("Z","+00:00"))
            except Exception: continue
            if ts>=now:
                future.append(item)
            if len(future)>=24: break
        if not future: future=series[:24]
        temps=[]; precip=[]; wind=[]; symbols=[]
        for item in future:
            d=(item.get("data") or {})
            instant=((d.get("instant") or {}).get("details") or {})
            if instant.get("air_temperature") is not None: temps.append(instant["air_temperature"])
            if instant.get("wind_speed") is not None: wind.append(instant["wind_speed"])
            one=((d.get("next_1_hours") or {}).get("details") or {})
            if one.get("precipitation_amount") is not None: precip.append(one["precipitation_amount"])
            sym=((d.get("next_1_hours") or {}).get("summary") or {}).get("symbol_code")
            if sym: symbols.append(sym)
        return {"ok":True,"status":"success","provider":"MET Norway Locationforecast 2.0",
                "source":"api.met.no","license":"CC BY 4.0","retrieved_at":datetime.now(timezone.utc).isoformat(),
                "location":geo,"hours":len(future),
                "forecast":{"temp_min_c":min(temps) if temps else None,"temp_max_c":max(temps) if temps else None,
                            "precipitation_mm":round(sum(precip),1) if precip else 0.0,
                            "wind_max_m_s":max(wind) if wind else None,
                            "symbols":list(dict.fromkeys(symbols))[:5]}}
    except Exception as exc:
        return {"ok":False,"status":"forecast_error","reason":type(exc).__name__,"location":geo}

def weather_reply(result, lang="sw"):
    if not result.get("ok"): return None
    f=result["forecast"]; place=result["location"].get("query")
    rain=f.get("precipitation_mm"); lo=f.get("temp_min_c"); hi=f.get("temp_max_c"); wind=f.get("wind_max_m_s")
    if lang=="en":
        return (f"🌦️ Live forecast for {place} (next ~{result['hours']} hours): "
                f"about {rain} mm precipitation, temperature {lo}–{hi}°C, max wind {wind} m/s. "
                f"Source: MET Norway; retrieved {result['retrieved_at']}. "
                "Tell me whether you are deciding about planting, spraying, harvesting or drying and I’ll interpret this for the farm.")
    return (f"🌦️ Forecast live ya {place} (takriban saa {result['hours']} zijazo): "
            f"mvua karibu {rain} mm, temperature {lo}–{hi}°C, upepo max {wind} m/s. "
            f"Source: MET Norway; retrieved {result['retrieved_at']}. "
            "Niambie decision ni kupanda, kuspray, kuvuna ama kukausha nikupe maana yake kwa shamba.")
