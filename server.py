import http.server
import socketserver
import json
import urllib.parse
import re
import os
import time
import threading
from datetime import datetime
import requests
from bs4 import BeautifulSoup

PORT = int(os.environ.get("PORT", 5000))
BASE_URL = "https://dtcc.phanmemdaotao.edu.vn/Pages/Sims/ScheduleOfClass.aspx?pt=4"
public_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

# Thread locks and in-memory TTL cache
SCRAPE_LOCK = threading.Lock()
CACHE_LOCK = threading.Lock()
DATA_CACHE = {}

def get_from_cache(key, ttl_seconds):
    with CACHE_LOCK:
        if key in DATA_CACHE:
            ts, val = DATA_CACHE[key]
            if time.time() - ts < ttl_seconds:
                return val
    return None

def set_to_cache(key, val):
    with CACHE_LOCK:
        DATA_CACHE[key] = (time.time(), val)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

PERIOD_TIMES = {
    1: ("07:15", "08:00"),
    2: ("08:00", "08:45"),
    3: ("09:15", "10:00"),
    4: ("10:00", "10:45"),
    5: ("10:45", "11:30"),
    6: ("11:30", "12:15"),
    7: ("13:15", "14:00"),
    8: ("14:00", "14:45"),
    9: ("15:15", "16:00"),
    10: ("16:00", "16:45"),
    11: ("16:45", "17:30"),
    12: ("17:30", "18:15"),
}

def calc_lesson_time(start_period, count):
    end_period = start_period + count - 1
    start = PERIOD_TIMES.get(start_period, ("07:15", "08:00"))[0]
    end = PERIOD_TIMES.get(end_period, ("10:45", "10:45"))[1]
    return start, end

cached_form_fields = {}

def update_fields(soup):
    global cached_form_fields
    for inp in soup.find_all("input"):
        name = inp.get("name")
        val = inp.get("value", "")
        if name:
            cached_form_fields[name] = val
    for sel in soup.find_all("select"):
        name = sel.get("name")
        if name:
            opt = sel.find("option", selected=True) or sel.find("option")
            cached_form_fields[name] = opt.get("value", "") if opt else ""

def get_initial():
    cached = get_from_cache("initial", 3600)
    if cached is not None:
        return cached

    with SCRAPE_LOCK:
        cached = get_from_cache("initial", 3600)
        if cached is not None:
            return cached

        r = session.get(BASE_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        update_fields(soup)
    
    faculties = []
    f_sel = soup.find("select", id="ctl00_cphMain_ScheduleOfClass1_uScheduleOfClass1_UClass1_ddlScienceID")
    if f_sel:
        for opt in f_sel.find_all("option"):
            v = opt.get("value", "").strip()
            if v:
                faculties.append({"id": v, "name": opt.text.strip()})
                
    weeks = []
    w_sel = soup.find("select", id="ctl00_cphMain_ScheduleOfClass1_uScheduleOfClass1_ddlWeek")
    week_pat = re.compile(r'(\d+)\s*\(\s*(\d{2}/\d{2})\s*-\s*(\d{2}/\d{2})\s*\)')
    if w_sel:
        for opt in w_sel.find_all("option"):
            v = opt.get("value", "").strip()
            t = opt.text.strip()
            m = week_pat.search(t)
            if m:
                weeks.append({
                    "id": v,
                    "text": t,
                    "weekNumber": int(m.group(1)),
                    "startDate": m.group(2),
                    "endDate": m.group(3)
                })
            elif v:
                weeks.append({
                    "id": v,
                    "text": t,
                    "weekNumber": int(v) if v.isdigit() else 1,
                    "startDate": "",
                    "endDate": ""
                })
                
    now = datetime.now()
    default_idx = 0
    for idx, w in enumerate(weeks):
        if w["startDate"] and w["endDate"]:
            try:
                s_d, s_m = map(int, w["startDate"].split("/"))
                e_d, e_m = map(int, w["endDate"].split("/"))
                s_y = now.year - 1 if (s_m > e_m and now.month <= e_m) else now.year
                e_y = now.year + 1 if (s_m > e_m and now.month >= s_m) else now.year
                dt_start = datetime(s_y, s_m, s_d)
                dt_end = datetime(e_y, e_m, e_d, 23, 59, 59)
                if dt_start <= now <= dt_end:
                    default_idx = idx
                    break
            except:
                pass
                
    initial_courses = []
    c_sel = soup.find("select", id="ctl00_cphMain_ScheduleOfClass1_uScheduleOfClass1_UClass1_ddlCourseID")
    if c_sel:
        for opt in c_sel.find_all("option"):
            v = opt.get("value", "").strip()
            if v:
                initial_courses.append({"id": v, "name": opt.text.strip()})

    initial_classes = []
    cl_sel = soup.find("select", id="ctl00_cphMain_ScheduleOfClass1_uScheduleOfClass1_UClass1_ddlClassID")
    if cl_sel:
        for opt in cl_sel.find_all("option"):
            v = opt.get("value", "").strip()
            if v:
                initial_classes.append({"id": v, "name": opt.text.strip()})

    res = {
        "faculties": faculties,
        "weeks": weeks,
        "defaultWeekIndex": default_idx,
        "initialCourses": initial_courses,
        "initialClasses": initial_classes
    }
    set_to_cache("initial", res)
    return res

def get_courses_for_faculty(faculty_id):
    if not cached_form_fields:
        get_initial()
    p = dict(cached_form_fields)
    p["__EVENTTARGET"] = "ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlScienceID"
    p["__EVENTARGUMENT"] = ""
    p["ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlScienceID"] = faculty_id
    p.pop("ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$btnSearch", None)
    
    r = session.post(BASE_URL, data=p, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    update_fields(soup)
    
    courses = []
    course_sel = soup.find("select", id="ctl00_cphMain_ScheduleOfClass1_uScheduleOfClass1_UClass1_ddlCourseID")
    if course_sel:
        for opt in course_sel.find_all("option"):
            v = opt.get("value", "").strip()
            if v:
                courses.append({"id": v, "name": opt.text.strip()})
                
    classes = []
    class_sel = soup.find("select", id="ctl00_cphMain_ScheduleOfClass1_uScheduleOfClass1_UClass1_ddlClassID")
    if class_sel:
        for opt in class_sel.find_all("option"):
            v = opt.get("value", "").strip()
            if v:
                classes.append({"id": v, "name": opt.text.strip()})
                
    return {"courses": courses, "classes": classes}

def get_classes_for_course(faculty_id, course_id):
    if not cached_form_fields:
        get_initial()
    if faculty_id and cached_form_fields.get("ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlScienceID") != faculty_id:
        get_courses_for_faculty(faculty_id)
    p = dict(cached_form_fields)

    p["__EVENTTARGET"] = "ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlCourseID"
    p["__EVENTARGUMENT"] = ""
    p["ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlScienceID"] = faculty_id or ""
    p["ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlCourseID"] = course_id
    p.pop("ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$btnSearch", None)
    
    r = session.post(BASE_URL, data=p, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    update_fields(soup)
    
    classes = []
    class_sel = soup.find("select", id="ctl00_cphMain_ScheduleOfClass1_uScheduleOfClass1_UClass1_ddlClassID")
    if class_sel:
        for opt in class_sel.find_all("option"):
            v = opt.get("value", "").strip()
            if v:
                classes.append({"id": v, "name": opt.text.strip()})
    return classes

def get_schedule(faculty_id, course_id, class_id, week_id, is_current_week):
    cache_key = f"sched_{class_id}_{week_id}"
    cached = get_from_cache(cache_key, 900)
    if cached is not None:
        return cached

    with SCRAPE_LOCK:
        cached = get_from_cache(cache_key, 900)
        if cached is not None:
            return cached

        if not cached_form_fields:
            get_initial()
        if faculty_id and cached_form_fields.get("ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlScienceID") != faculty_id:
            get_courses_for_faculty(faculty_id)
        if course_id and cached_form_fields.get("ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlCourseID") != course_id:
            get_classes_for_course(faculty_id, course_id)
            
        p = dict(cached_form_fields)
        p["__EVENTTARGET"] = ""
        p["__EVENTARGUMENT"] = ""
        p["ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlScienceID"] = faculty_id
        if course_id:
            p["ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlCourseID"] = course_id
    p["ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$ddlClassID"] = class_id
    p["ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$ddlWeek"] = week_id
    p["ctl00$cphMain$ScheduleOfClass1$uScheduleOfClass1$UClass1$btnSearch"] = "Tìm kiếm"
    p.pop("ctl00$MsgBox$btnOk", None)
    p.pop("ctl00$MsgBox$imgCloseButton", None)
    
    r = session.post(BASE_URL, data=p, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    update_fields(soup)
    
    lbl = soup.find(id="ctl00_cphMain_ScheduleOfClass1_uScheduleOfClass1_lblContent")
    if not lbl:
        return []
    table = lbl.find("table")
    if not table:
        return []
        
    rows = table.find_all("tr")
    if len(rows) <= 2:
        return []

    # Định vị các dòng thuộc class_id nếu bảng trả về nhiều lớp
    target_rows = rows
    if class_id:
        start_idx = -1
        class_rowspan = 12
        for idx, r in enumerate(rows):
            cells = r.find_all(["td", "th"])
            if cells and cells[0].get_text(strip=True).lower() == class_id.lower():
                start_idx = idx
                class_rowspan = int(cells[0].get("rowspan", 12))
                break
        if start_idx != -1:
            collected = []
            max_end = min(start_idx + class_rowspan, len(rows))
            for r_idx in range(start_idx, max_end):
                r = rows[r_idx]
                cells = r.find_all(["td", "th"])
                if r_idx > start_idx and cells:
                    first_text = cells[0].get_text(strip=True)
                    rowspan = int(cells[0].get("rowspan", 1))
                    if (rowspan >= 6 and first_text not in ("Sáng", "Chiều", "Tối") and first_text.lower() != class_id.lower()) or first_text == "Mã lớp":
                        break
                collected.append(r)
            target_rows = collected

    lessons = []
    active_rowspans = [0] * 7
    day_names = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]

    for row in target_rows:
        cells = row.find_all(["td", "th"])

        # Tìm ô số tiết (1..12)
        period_idx = -1
        period_num = -1
        for idx, c in enumerate(cells):
            t = c.get_text(strip=True)
            if re.match(r"^\d{1,2}$", t):
                val = int(t)
                if 1 <= val <= 12:
                    period_idx = idx
                    period_num = val
                    break

        if period_idx == -1:
            continue

        if period_num == 7 or period_num == 13:
            active_rowspans = [0] * 7

        data_cells = cells[period_idx + 1:]

        cell_ptr = 0
        for day_idx in range(7):
            if active_rowspans[day_idx] > 0:
                active_rowspans[day_idx] -= 1
                continue
            if cell_ptr < len(data_cells):
                cell = data_cells[cell_ptr]
                cell_ptr += 1
                rowspan = int(cell.get("rowspan", 1))
                active_rowspans[day_idx] = rowspan - 1

                raw_lines = re.sub(r"(?i)<br\s*/?>|<hr\s*/?>", "\n", str(cell))
                lines = [BeautifulSoup(line, "html.parser").get_text().strip() for line in raw_lines.split("\n")]
                lines = [l for l in lines if l]
                if lines:
                    items = []
                    current_chunk = []
                    for line in lines:
                        if re.search(r"\d{1,2}:\d{2}", line):
                            if current_chunk:
                                subj = current_chunk[0]
                                rm = current_chunk[1] if len(current_chunk) > 1 else ""
                                tc = current_chunk[2] if len(current_chunk) > 2 else ""
                                items.append((subj, rm, tc))
                                current_chunk = []
                        else:
                            current_chunk.append(line)
                    if current_chunk:
                        subj = current_chunk[0]
                        rm = current_chunk[1] if len(current_chunk) > 1 else ""
                        tc = current_chunk[2] if len(current_chunk) > 2 else ""
                        items.append((subj, rm, tc))

                    calc_s, calc_e = calc_lesson_time(period_num, rowspan)
                    for subj, rm, tc in items:
                        clean_rm = rm.strip()
                        clean_tc = f"GV: {tc}" if (tc and not tc.lower().startswith("gv")) else tc
                        lessons.append({
                            "subject": subj,
                            "room": clean_rm,
                            "teacher": clean_tc,
                            "startPeriod": period_num,
                            "endPeriod": period_num + rowspan - 1,
                            "periodCount": rowspan,
                            "startTime": calc_s,
                            "endTime": calc_e,
                            "dayIndex": day_idx,
                            "dayName": day_names[day_idx]
                        })
                    
    now = datetime.now()
    today_idx = now.weekday() # 0 = Mon ... 6 = Sun
    
    day_schedules = []
    for day_idx in range(7):
        day_lessons = [l for l in lessons if l["dayIndex"] == day_idx]
        day_lessons.sort(key=lambda x: x["startPeriod"])
        day_schedules.append({
            "dayName": day_names[day_idx],
            "dayIndex": day_idx,
            "isToday": bool(is_current_week and (day_idx == today_idx)),
            "lessons": day_lessons
        })
    set_to_cache(cache_key, day_schedules)
    return day_schedules

import gzip
import hashlib

class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=public_dir, **kwargs)

    def send_compressed_response(self, content_bytes, content_type, cache_control="public, max-age=3600", status=200, is_service_worker=False):
        etag = f'"{hashlib.md5(content_bytes).hexdigest()}"'

        # Check ETag / Conditional GET (trả về 304 Not Modified = 0 byte dữ liệu)
        if not is_service_worker and status == 200:
            if_none_match = self.headers.get("If-None-Match")
            if if_none_match and if_none_match.strip() == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", cache_control)
                self.end_headers()
                return

        accept_encoding = self.headers.get("Accept-Encoding", "")
        use_gzip = "gzip" in accept_encoding and len(content_bytes) > 150

        if use_gzip:
            payload = gzip.compress(content_bytes, compresslevel=6)
        else:
            payload = content_bytes

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        if not is_service_worker:
            self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Access-Control-Allow-Origin", "*")
        if is_service_worker:
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # Service Worker: Always 200 OK without 304, no-cache
        if path == "/service-worker.js":
            sw_path = os.path.join(public_dir, "service-worker.js")
            if os.path.exists(sw_path):
                with open(sw_path, "rb") as f:
                    content = f.read()
                self.send_compressed_response(
                    content,
                    "application/javascript; charset=utf-8",
                    cache_control="no-cache, no-store, must-revalidate",
                    is_service_worker=True
                )
                return

        # HTML Trang chủ (Luôn revalidate để nhận giao diện mới nhất, ETag trả 304 nếu không đổi)
        if path == "/" or path == "/index.html":
            html_path = os.path.join(public_dir, "index.html")
            if os.path.exists(html_path):
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_compressed_response(
                    content,
                    "text/html; charset=utf-8",
                    cache_control="no-cache, must-revalidate"
                )
                return

        # PWA Manifest
        if path == "/manifest.json":
            m_path = os.path.join(public_dir, "manifest.json")
            if os.path.exists(m_path):
                with open(m_path, "rb") as f:
                    content = f.read()
                self.send_compressed_response(
                    content,
                    "application/manifest+json; charset=utf-8",
                    cache_control="public, max-age=86400"
                )
                return

        # Ảnh tĩnh PNG (Cache vĩnh viễn 30 ngày trên trình duyệt = 0 byte lần sau)
        if path.endswith(".png"):
            file_name = os.path.basename(path)
            img_path = os.path.join(public_dir, file_name)
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    content = f.read()
                self.send_compressed_response(
                    content,
                    "image/png",
                    cache_control="public, max-age=2592000, immutable"
                )
                return

        # API Khởi tạo
        if path == "/api/init":
            try:
                data = get_initial()
                json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_compressed_response(
                    json_bytes,
                    "application/json; charset=utf-8",
                    cache_control="public, max-age=600"
                )
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return
            
        elif path == "/api/courses":
            faculty_id = qs.get("faculty", [""])[0]
            try:
                data = get_courses_for_faculty(faculty_id)
                json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_compressed_response(
                    json_bytes,
                    "application/json; charset=utf-8",
                    cache_control="public, max-age=1800"
                )
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return

        elif path == "/api/classes":
            faculty_id = qs.get("faculty", [""])[0]
            course_id = qs.get("course", [""])[0]
            try:
                if course_id:
                    classes = get_classes_for_course(faculty_id, course_id)
                else:
                    classes = get_courses_for_faculty(faculty_id)["classes"]
                json_bytes = json.dumps(classes, ensure_ascii=False).encode("utf-8")
                self.send_compressed_response(
                    json_bytes,
                    "application/json; charset=utf-8",
                    cache_control="public, max-age=1800"
                )
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return
            
        elif path == "/api/schedule":
            faculty_id = qs.get("faculty", [""])[0]
            course_id = qs.get("course", [""])[0]
            class_id = qs.get("class", [""])[0]
            week_id = qs.get("week", ["0"])[0]
            is_cur = qs.get("isCurrent", ["true"])[0].lower() == "true"
            try:
                schedules = get_schedule(faculty_id, course_id, class_id, week_id, is_cur)
                json_bytes = json.dumps(schedules, ensure_ascii=False).encode("utf-8")
                self.send_compressed_response(
                    json_bytes,
                    "application/json; charset=utf-8",
                    cache_control="public, max-age=300"
                )
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return
            
        super().do_GET()

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), RequestHandler) as httpd:
        print(f"Server started at http://localhost:{PORT}")
        httpd.serve_forever()

