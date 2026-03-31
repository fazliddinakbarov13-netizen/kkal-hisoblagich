import sys, os
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

import io, json, re, logging, random, threading, time as _time
from datetime import datetime, date, timedelta
import telebot
from telebot import types
import google.generativeai as genai
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========== SOZLAMALAR ===========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8512598853:AAEJ_3-WjR26vwMCISZIylZe1l-RPrBPeXo")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAm58_gHgklFDdZKnPbrplMeeA19JQN-to")
# ==================================

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

DATA_FILE = "user_data.json"

MOTIVATIONS = [
    "Bugun ham sog'lom ovqatlaning! Siz buni qila olasiz!",
    "Har bir sog'lom tanlov kelajakka investitsiya!",
    "Kichik qadamlar - katta natijalar!",
    "Tanangiz - uyingiz. Uni yaxshi ovqatlantiring!",
    "Muvaffaqiyat - bu har kuni to'g'ri qaror qilish!",
    "Sog'liq - bu boylik. Uni asrang!",
    "Natija ko'rish uchun sabr kerak. Davom eting!",
    "Siz allaqachon yaxshi yo'ldasiz!",
]

EXERCISES = {
    "yugurish": {"kcal_per_min": 10, "name": "Yugurish"},
    "yurish": {"kcal_per_min": 4, "name": "Yurish"},
    "velosiped": {"kcal_per_min": 8, "name": "Velosiped"},
    "suzish": {"kcal_per_min": 9, "name": "Suzish"},
    "yoga": {"kcal_per_min": 3, "name": "Yoga"},
    "kuchmashq": {"kcal_per_min": 7, "name": "Kuch mashqlari"},
    "futbol": {"kcal_per_min": 9, "name": "Futbol"},
    "basket": {"kcal_per_min": 8, "name": "Basketbol"},
    "raqs": {"kcal_per_min": 6, "name": "Raqs"},
    "skakalka": {"kcal_per_min": 12, "name": "Skakalka"},
    "planka": {"kcal_per_min": 5, "name": "Planka"},
    "uyishi": {"kcal_per_min": 3, "name": "Uy ishlari"},
}

# ===== YORDAMCHI =====
def escape_md(text):
    if not text: return ""
    return re.sub(r'([_*`\[\]])', r'\\\1', str(text))

def load_data():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return {}

def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except: pass

DEFAULT_USER = {
    "goal": 2000, "history": {}, "water": {}, "water_goal": 8,
    "weight_log": {}, "exercises": {}, "streak": 0, "last_log_date": "",
    "profile": {}, "health": [], "reminders": True, "referrals": [],
    "referral_code": "", "premium": False, "premium_until": "",
    "daily_photo_count": {}, "joined": "",
}

def get_user(uid):
    uid = str(uid)
    data = load_data()
    if uid not in data:
        user = DEFAULT_USER.copy()
        for k,v in DEFAULT_USER.items():
            if isinstance(v, (dict,list)):
                user[k] = type(v)()
        user["joined"] = get_today()
        user["referral_code"] = f"KH{uid[-6:]}"
        data[uid] = user
        save_data(data)
    else:
        for k,v in DEFAULT_USER.items():
            if k not in data[uid]:
                data[uid][k] = type(v)() if isinstance(v,(dict,list)) else v
        save_data(data)
    return data[uid]

def save_user(uid, user_data):
    uid = str(uid)
    data = load_data()
    data[uid] = user_data
    save_data(data)

def get_today():
    return date.today().isoformat()

def progress_bar(current, total, length=12):
    pct = min(current/total, 1.0) if total > 0 else 0
    filled = round(pct * length)
    return "#" * filled + "-" * (length - filled) + f" {int(pct*100)}%"

def update_streak(user):
    today = get_today()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    last = user.get("last_log_date","")
    if last == today: return
    elif last == yesterday: user["streak"] = user.get("streak",0)+1
    else: user["streak"] = 1
    user["last_log_date"] = today

def calc_bmi(weight, height_cm):
    if height_cm<=0 or weight<=0: return 0,""
    h=height_cm/100; bmi=weight/(h*h)
    if bmi<18.5: cat="Kam vazn"
    elif bmi<25: cat="Normal"
    elif bmi<30: cat="Ortiqcha vazn"
    else: cat="Semizlik"
    return round(bmi,1), cat

def check_premium(user):
    if not user.get("premium"): return False
    until = user.get("premium_until","")
    if until and until < get_today():
        user["premium"]=False; return False
    return True

# ===== GEMINI =====
ANALYSIS_PROMPT = """Sen ovqat mutaxassisisisan. Rasmda ko'rsatilgan ovqatni tahlil qil.
Javobni FAQAT JSON formatda ber:
{"food_name":"Ovqat nomi","food_name_detail":"Tavsif","estimated_weight_g":250,"calories_kcal":450,"protein_g":20,"fat_g":15,"carbs_g":55,"fiber_g":3,"items":[{"name":"Qism 1","kcal":200,"gram":100}],"confidence":"yuqori","health_warnings":[],"notes":"Izoh"}
Qoidalar:
- Ovqat yo'q bo'lsa food_name ga "Ovqat topilmadi" yoz
- O'zbek ovqatlarini bil (osh, somsa, manti, lag'mon, shashlik)
- health_warnings ga ogohlantirishlar yoz
- Faqat JSON"""

def analyze_food(photo_bytes):
    try:
        img = Image.open(io.BytesIO(photo_bytes))
        if img.width>1024 or img.height>1024:
            img.thumbnail((1024,1024), Image.LANCZOS)
        response = model.generate_content([ANALYSIS_PROMPT, img])
        if not response or not response.text:
            return {"error": "AI javob bermadi."}
        text = response.text.strip()
        if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
        result = json.loads(text)
        for k in ["food_name","calories_kcal","protein_g","fat_g","carbs_g"]:
            if k not in result: result[k] = 0 if k!="food_name" else "Nomalum"
        return result
    except json.JSONDecodeError:
        return {"error":"AI javobini tushunib bolmadi."}
    except Exception as e:
        return {"error":str(e)}

def format_result(r):
    if "error" in r: return "Xatolik: "+escape_md(r['error'])
    if "topilmadi" in r.get("food_name","").lower():
        return "Rasmda ovqat topilmadi. Ovqat suratini yuboring."
    name=escape_md(r.get("food_name","?"))
    detail=escape_md(r.get("food_name_detail",""))
    w=r.get("estimated_weight_g",0); k=r.get("calories_kcal",0)
    p=r.get("protein_g",0); f=r.get("fat_g",0); c=r.get("carbs_g",0)
    fb=r.get("fiber_g",0); conf=escape_md(r.get("confidence",""))
    items=r.get("items",[]); notes=escape_md(r.get("notes",""))
    text=f"*{name}*\n"
    if detail: text+=f"{detail}\n"
    text+=f"\nVazn: *{w}g*\n\n"
    text+=f"Kaloriya: *{k} kkal*\nOqsil: *{p}g*\nYog: *{f}g*\nUglevod: *{c}g*\nTolalar: *{fb}g*\n"
    if items:
        text+="\nTarkibi:\n"
        for it in items:
            text+=f"  - {escape_md(it.get('name',''))}: {it.get('kcal',0)} kkal\n"
    text+=f"\nAniqlik: *{conf}*"
    if notes: text+=f"\n{notes}"
    return text

def get_ai_advice(user):
    today=get_today()
    tl=user.get("history",{}).get(today,[])
    if not tl: return "Hali ovqat yemagansiz. Nonushta qiling!"
    tk=sum(e["kcal"] for e in tl); tp=sum(e["protein"] for e in tl)
    tf=sum(e["fat"] for e in tl); tc=sum(e["carbs"] for e in tl)
    goal=user.get("goal",2000); rem=goal-tk
    prompt=f"Sen dietologsisan. Qisqa maslahat ber (3 gap, o'zbek tilida).\nKaloriya: {round(tk)}/{goal} kkal, Oqsil: {round(tp)}g, Yog: {round(tf)}g, Uglevod: {round(tc)}g, Qoldi: {round(rem)} kkal"
    try:
        resp=model.generate_content(prompt)
        if resp and resp.text: return resp.text.strip()
    except: pass
    if rem>500: return f"Hali {round(rem)} kkal qoldi. Soglom ovqat yeng!"
    return "Yaxshi natija! Davom eting!"

# Foydalanuvchi holatlari
user_states = {}

# ===== HANDLERS =====
@bot.message_handler(commands=['start'])
def start(msg):
    uid=str(msg.chat.id); user=get_user(uid)
    # Referral
    args=msg.text.split()
    if len(args)>1:
        ref=args[1]; data=load_data()
        for ruid,ru in data.items():
            if ru.get("referral_code")==ref and ruid!=uid:
                if uid not in ru.get("referrals",[]):
                    ru.setdefault("referrals",[]).append(uid)
                    until=(date.today()+timedelta(days=7)).isoformat()
                    ru["premium"]=True; ru["premium_until"]=until
                    user["premium"]=True; user["premium_until"]=until
                    save_user(ruid,ru); save_user(uid,user)
                    bot.send_message(uid,"Tabriklaymiz! 7 kunlik Premium berildi!")
                break
    kb=types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Bugungi hisobot","Suv")
    kb.row("Mashq","Vazn")
    kb.row("Maqsad","Sozlamalar")
    streak=user.get("streak",0)
    s_text=f"\nSeriyangiz: *{streak}* kun" if streak>0 else ""
    bot.send_message(uid,
        "*Kaloriya Hisoblagich Bot*\n\n"
        "Ovqatingizni suratga olib yuboring!\n"
        "AI kaloriya, oqsil, yog, uglevod aytadi.\n\n"
        "*Imkoniyatlar:*\n"
        "- AI ovqat tahlili\n- Suv kuzatish\n- Mashq kuzatish\n"
        "- Vazn va BMI\n- Haftalik hisobot\n- Streak tizimi\n"
        f"- AI maslahat{s_text}",
        reply_markup=kb
    )

@bot.message_handler(commands=['help'])
def help_cmd(msg):
    bot.send_message(msg.chat.id,
        "*Yordam*\n\n"
        "Rasm yuborish - AI tahlil qiladi\n"
        "Bugungi hisobot - kunlik natija\n"
        "Suv - suv ichish kuzatish\n"
        "Mashq - sport kuzatish\n"
        "Vazn - vazn va BMI\n"
        "/goal 2000 - maqsad belgilash\n"
        "Sozlamalar - eslatma, referral"
    )

@bot.message_handler(commands=['goal'])
def goal_cmd(msg):
    uid=str(msg.chat.id); parts=msg.text.split()
    if len(parts)>=2:
        try:
            g=int(parts[1])
            if 500<=g<=10000:
                user=get_user(uid); user["goal"]=g; save_user(uid,user)
                bot.send_message(uid,f"Maqsad: *{g} kkal* belgilandi!")
                return
        except: pass
    bot.send_message(uid,"*Maqsad belgilash*\n`/goal 2000`\n\n- Vazn yoqotish: 1500-1800\n- Saqlash: 2000-2500\n- Olish: 2800-3500")

# ===== RASM =====
@bot.message_handler(content_types=['photo'])
def handle_photo(msg):
    uid=str(msg.chat.id); user=get_user(uid)
    is_prem=check_premium(user)
    cnt=user.get("daily_photo_count",{}).get(get_today(),0)
    if not is_prem and cnt>=10:
        bot.send_message(uid,"Bugungi limit tugadi (10/kun). Premium uchun /start")
        return
    wait=bot.send_message(uid,"*Tahlil qilinmoqda...*\nAI ovqatni aniqlayapti...")
    try:
        file_info=bot.get_file(msg.photo[-1].file_id)
        photo_bytes=bot.download_file(file_info.file_path)
        result=analyze_food(photo_bytes)
        if "error" in result:
            bot.edit_message_text(f"Xatolik: {result['error']}",uid,wait.message_id)
            return
        user_states[uid]={"result":result}
        user.setdefault("daily_photo_count",{})[get_today()]=cnt+1
        save_user(uid,user)
        text=format_result(result)
        kb=types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("Hisobga qoshish",callback_data="cf_add"),
               types.InlineKeyboardButton("Bekor",callback_data="cf_no"))
        try: bot.edit_message_text(text,uid,wait.message_id,parse_mode="Markdown",reply_markup=kb)
        except: bot.edit_message_text(text,uid,wait.message_id,reply_markup=kb)
    except Exception as e:
        bot.edit_message_text(f"Xatolik: {str(e)}",uid,wait.message_id)

# ===== CALLBACKS =====
@bot.callback_query_handler(func=lambda c: True)
def callback_handler(call):
    uid=str(call.message.chat.id); d=call.data
    bot.answer_callback_query(call.id)

    if d=="cf_add":
        st=user_states.get(uid,{})
        r=st.get("result")
        if not r: bot.edit_message_text("Malumot topilmadi.",uid,call.message.id); return
        user=get_user(uid); today=get_today()
        user.setdefault("history",{}).setdefault(today,[])
        entry={"name":r.get("food_name","?"),"kcal":r.get("calories_kcal",0),
               "protein":r.get("protein_g",0),"fat":r.get("fat_g",0),
               "carbs":r.get("carbs_g",0),"weight":r.get("estimated_weight_g",0),
               "time":datetime.now().strftime("%H:%M")}
        user["history"][today].append(entry); update_streak(user); save_user(uid,user)
        tl=user["history"][today]; tk=sum(e["kcal"] for e in tl)
        goal=user.get("goal",2000); rem=max(goal-tk,0)
        bar=progress_bar(tk,goal); streak=user.get("streak",0)
        s_msg=f"\nSeriya: *{streak}* kun" if streak>1 else ""
        h=datetime.now().hour
        t_note=""
        if h>=22 or h<5: t_note="\n\nTungi ovqat vazn olishga olib keladi!"
        elif h>=20: t_note="\nKechki ovqatni yengilroq yeng."
        bot.edit_message_text(
            f"*Qoshildi!*\n\n{escape_md(entry['name'])} - *{entry['kcal']} kkal*\n\n"
            f"Bugungi: *{round(tk)}*/{goal} kkal\n`{bar}`\n"
            f"Qoldi: *{round(rem)}* kkal\nJami: *{len(tl)}* ta{s_msg}{t_note}",
            uid,call.message.id,parse_mode="Markdown")
        user_states.pop(uid,None)

    elif d=="cf_no":
        bot.edit_message_text("Bekor qilindi.",uid,call.message.id)
        user_states.pop(uid,None)

    # SUV
    elif d.startswith("w_"):
        user=get_user(uid); today=get_today()
        user.setdefault("water",{})
        if d=="w_reset": user["water"][today]=0
        else:
            amt=int(d.split("_")[1]); user["water"][today]=user["water"].get(today,0)+amt
        save_user(uid,user)
        cur=user["water"].get(today,0); wg=user.get("water_goal",8)
        bar=progress_bar(cur,wg,8)
        done="" if cur<wg else "\n\nAjoyib! Suv normasini bajardingiz!"
        kb=types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("+1",callback_data="w_1"),
               types.InlineKeyboardButton("+2",callback_data="w_2"),
               types.InlineKeyboardButton("+3",callback_data="w_3"))
        kb.row(types.InlineKeyboardButton("Tiklash",callback_data="w_reset"))
        bot.edit_message_text(f"*Suv*\n\nBugun: *{cur}*/{wg} stakan\n`{bar}`{done}",
            uid,call.message.id,parse_mode="Markdown",reply_markup=kb)

    # MASHQ TANLASH
    elif d.startswith("ex_"):
        if d=="ex_today":
            user=get_user(uid); exs=user.get("exercises",{}).get(get_today(),[])
            if not exs: bot.edit_message_text("Bugun mashq yoq.",uid,call.message.id); return
            total=sum(e["kcal"] for e in exs)
            items="".join(f"  - {e['name']} ({e['min']}min) = *{e['kcal']}* kkal\n" for e in exs)
            bot.edit_message_text(f"*Bugungi mashqlar*\n\n{items}\nJami: *{total}* kkal",
                uid,call.message.id,parse_mode="Markdown")
        elif d.startswith("ex_m_"):
            parts=d.split("_"); exk="_".join(parts[2:-1]); mins=int(parts[-1])
            ex=EXERCISES.get(exk)
            if not ex: bot.edit_message_text("Topilmadi.",uid,call.message.id); return
            kcal=ex["kcal_per_min"]*mins
            user=get_user(uid); today=get_today()
            user.setdefault("exercises",{}).setdefault(today,[])
            user["exercises"][today].append({"name":ex["name"],"min":mins,"kcal":kcal,"time":datetime.now().strftime("%H:%M")})
            save_user(uid,user)
            bot.edit_message_text(f"*Saqlandi!*\n{ex['name']} - {mins} min\nSarflangan: *{kcal}* kkal",
                uid,call.message.id,parse_mode="Markdown")
        else:
            exk=d.replace("ex_","")
            ex=EXERCISES.get(exk)
            if not ex: return
            kb=types.InlineKeyboardMarkup()
            kb.row(*[types.InlineKeyboardButton(f"{m}min",callback_data=f"ex_m_{exk}_{m}") for m in [10,20,30]])
            kb.row(*[types.InlineKeyboardButton(f"{m}min",callback_data=f"ex_m_{exk}_{m}") for m in [45,60,90]])
            bot.edit_message_text(f"*{ex['name']}*\nNecha daqiqa?",uid,call.message.id,
                parse_mode="Markdown",reply_markup=kb)

    # VAZN
    elif d=="wt_input":
        user_states[uid]={"waiting":"weight"}
        bot.edit_message_text("Vaznizni kiriting (masalan: 72.5):",uid,call.message.id)
    elif d=="wt_history":
        user=get_user(uid); wl=user.get("weight_log",{})
        if not wl: bot.edit_message_text("Vazn tarixi yoq.",uid,call.message.id); return
        dates=sorted(wl.keys())[-14:]; text="*Vazn tarixi:*\n\n"; prev=None
        for dt in dates:
            w=wl[dt]; arr=""
            if prev: arr=" (tushdi)" if w<prev else (" (oshdi)" if w>prev else "")
            text+=f"  {dt}: *{w}* kg{arr}\n"; prev=w
        if len(dates)>=2:
            diff=wl[dates[-1]]-wl[dates[0]]; text+=f"\nOzgarish: *{diff:+.1f}* kg"
        bot.edit_message_text(text,uid,call.message.id,parse_mode="Markdown")
    elif d=="wt_bmi":
        user=get_user(uid); h=user.get("profile",{}).get("height",0)
        wl=user.get("weight_log",{}); w=wl[sorted(wl.keys())[-1]] if wl else 0
        if h and w:
            bmi,cat=calc_bmi(w,h)
            bot.edit_message_text(f"*BMI*\nBoy: {h}sm, Vazn: {w}kg\nBMI: *{bmi}* - {cat}\nNormal: 18.5-24.9",
                uid,call.message.id,parse_mode="Markdown")
        else:
            user_states[uid]={"waiting":"bmi"}
            bot.edit_message_text("Boy va vazn kiriting: 175 72",uid,call.message.id)

    # AI MASLAHAT
    elif d=="ai_advice":
        user=get_user(uid); advice=get_ai_advice(user)
        bot.send_message(uid,f"*AI maslahat:*\n\n{escape_md(advice)}",parse_mode="Markdown")

    # HAFTALIK
    elif d=="weekly":
        user=get_user(uid); goal=user.get("goal",2000); hist=user.get("history",{})
        text="*Haftalik hisobot (7 kun)*\n\n"; tw=0; dl=0
        for i in range(7):
            dt=(date.today()-timedelta(days=6-i)).isoformat()
            dl2=hist.get(dt,[])
            dn=(date.today()-timedelta(days=6-i)).strftime("%d.%m")
            if dl2:
                dk=sum(e["kcal"] for e in dl2); tw+=dk; dl+=1
                sym="ok" if dk<=goal else "!"
                text+=f"  {dn}: *{round(dk)}* kkal [{sym}]\n"
            else: text+=f"  {dn}: --\n"
        if dl>0:
            avg=tw/dl; text+=f"\nOrtacha: *{round(avg)}* kkal/kun\nMaqsad: *{goal}*\n"
            text+=f"Farq: *{round(abs(avg-goal))}* kkal "+("kam" if avg<goal else "ortiq")
        bot.edit_message_text(text,uid,call.message.id,parse_mode="Markdown")

    # SOZLAMALAR
    elif d=="s_reminder":
        user=get_user(uid); user["reminders"]=not user.get("reminders",True); save_user(uid,user)
        bot.edit_message_text(f"Eslatma: {'Yoqildi' if user['reminders'] else 'Ochirildi'}",uid,call.message.id)
    elif d=="s_health":
        kb=types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("Diabet",callback_data="h_diabet"),
               types.InlineKeyboardButton("Gipertoniya",callback_data="h_giper"))
        kb.row(types.InlineKeyboardButton("Allergiya",callback_data="h_allergiya"),
               types.InlineKeyboardButton("Yoq",callback_data="h_none"))
        bot.edit_message_text("*Sogliq holat:*",uid,call.message.id,parse_mode="Markdown",reply_markup=kb)
    elif d.startswith("h_"):
        user=get_user(uid); h=d[2:]
        if h=="none": user["health"]=[]
        else: user.setdefault("health",[]); user["health"].append(h) if h not in user["health"] else None
        save_user(uid,user)
        bot.edit_message_text(f"Saqlandi: {', '.join(user['health']) or 'Yoq'}",uid,call.message.id)
    elif d=="s_water_goal":
        user_states[uid]={"waiting":"water_goal"}
        bot.edit_message_text("Suv normasi (stakan): ",uid,call.message.id)
    elif d=="s_referral":
        user=get_user(uid); code=user.get("referral_code","")
        refs=len(user.get("referrals",[])); me=bot.get_me()
        link=f"https://t.me/{me.username}?start={code}"
        bot.edit_message_text(f"*Referral*\n\nKod: `{code}`\nHavola: {link}\nTaklif: *{refs}* kishi\n\nHar taklif = 7 kun Premium!",
            uid,call.message.id,parse_mode="Markdown")
    elif d=="s_premium":
        user=get_user(uid)
        if check_premium(user):
            bot.edit_message_text(f"*Premium faol!*\nAmal: {user.get('premium_until','')}",uid,call.message.id,parse_mode="Markdown")
        else:
            bot.edit_message_text("*Premium*\nDostingizni taklif qiling = 7 kun bepul!",uid,call.message.id,parse_mode="Markdown")
    elif d=="s_clear":
        kb=types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("Ha",callback_data="clr_y"),types.InlineKeyboardButton("Yoq",callback_data="clr_n"))
        bot.edit_message_text("Bugunni tozalash?",uid,call.message.id,reply_markup=kb)
    elif d=="clr_y":
        user=get_user(uid); t=get_today()
        user.setdefault("history",{})[t]=[]; user.setdefault("water",{})[t]=0
        user.setdefault("exercises",{})[t]=[]; save_user(uid,user)
        bot.edit_message_text("Tozalandi!",uid,call.message.id)
    elif d=="clr_n":
        bot.edit_message_text("Bekor.",uid,call.message.id)

# ===== TEXT =====
@bot.message_handler(func=lambda m: True)
def text_handler(msg):
    uid=str(msg.chat.id); text=msg.text.strip()

    # Holat tekshirish
    st=user_states.get(uid,{})
    if st.get("waiting")=="weight":
        try:
            w=float(text.replace(",","."))
            if 20<=w<=300:
                user=get_user(uid); user.setdefault("weight_log",{})[get_today()]=w
                user.setdefault("profile",{})["weight"]=w; save_user(uid,user)
                bmi_text=""
                h=user.get("profile",{}).get("height",0)
                if h: bmi,cat=calc_bmi(w,h); bmi_text=f"\nBMI: *{bmi}* ({cat})"
                bot.send_message(uid,f"Vazn: *{w}* kg{bmi_text}",parse_mode="Markdown")
                user_states.pop(uid,None); return
        except: pass
        bot.send_message(uid,"Notogri. Masalan: 72.5"); user_states.pop(uid,None); return

    if st.get("waiting")=="bmi":
        try:
            p=text.split(); h=float(p[0]); w=float(p[1])
            bmi,cat=calc_bmi(w,h)
            user=get_user(uid); user.setdefault("profile",{})["height"]=h
            user["profile"]["weight"]=w; user.setdefault("weight_log",{})[get_today()]=w
            save_user(uid,user)
            bot.send_message(uid,f"*BMI*\nBoy: {h}sm, Vazn: {w}kg\nBMI: *{bmi}* - {cat}",parse_mode="Markdown")
            user_states.pop(uid,None); return
        except: pass
        bot.send_message(uid,"Notogri. Masalan: 175 72"); user_states.pop(uid,None); return

    if st.get("waiting")=="water_goal":
        try:
            g=int(text)
            if 1<=g<=20:
                user=get_user(uid); user["water_goal"]=g; save_user(uid,user)
                bot.send_message(uid,f"Suv norma: *{g}* stakan",parse_mode="Markdown")
                user_states.pop(uid,None); return
        except: pass
        bot.send_message(uid,"1-20 orasida raqam kiriting."); user_states.pop(uid,None); return

    # TUGMALAR
    if text=="Bugungi hisobot":
        user=get_user(uid); today=get_today()
        tl=user.get("history",{}).get(today,[]); goal=user.get("goal",2000)
        water=user.get("water",{}).get(today,0); wg=user.get("water_goal",8)
        exs=user.get("exercises",{}).get(today,[]); ex_kcal=sum(e["kcal"] for e in exs)
        streak=user.get("streak",0)
        if not tl and water==0 and not exs:
            bot.send_message(uid,f"*Bugungi hisobot*\n\nHali hech narsa yoq.\nMaqsad: *{goal}* kkal\n\nRasm yuboring!",parse_mode="Markdown")
            return
        tk=sum(e["kcal"] for e in tl); tp=sum(e["protein"] for e in tl)
        tf=sum(e["fat"] for e in tl); tc=sum(e["carbs"] for e in tl)
        net=tk-ex_kcal; rem=max(goal-net,0); bar=progress_bar(net,goal)
        status="Norma" if net<=goal else "Norma oshdi!"
        wb=progress_bar(water,wg,8)
        # Vaqt tahlili
        ta=""
        if tl:
            times={"e":0,"t":0,"k":0,"n":0}
            for e in tl:
                hr=int(e.get("time","12:00").split(":")[0])
                if 5<=hr<11: times["e"]+=e["kcal"]
                elif 11<=hr<16: times["t"]+=e["kcal"]
                elif 16<=hr<21: times["k"]+=e["kcal"]
                else: times["n"]+=e["kcal"]
            if tk>0:
                ta="\nOvqat vaqti:\n"
                for lb,v in [("Ertalab",times["e"]),("Tushlik",times["t"]),("Kechki",times["k"]),("Tungi",times["n"])]:
                    if v>0: ta+=f"  {lb}: {round(v)} kkal ({int(v/tk*100)}%)\n"
                if times["n"]>tk*0.1: ta+="  ! Tungi ovqat vazn oshiradi\n"
        items="".join(f"  {i+1}. {escape_md(e.get('name','?'))} - *{e['kcal']}* kkal ({e['time']})\n" for i,e in enumerate(tl))
        t2=f"*Bugungi hisobot*\n\nKaloriya: *{round(tk)}* kkal\n"
        if ex_kcal>0: t2+=f"Mashq: *{ex_kcal}* kkal\nSof: *{round(net)}*/{goal}\n"
        else: t2+=f"Maqsad: *{round(tk)}*/{goal}\n"
        t2+=f"`{bar}`\nQoldi: *{round(rem)}* kkal ({status})\n\n"
        t2+=f"Oqsil: *{round(tp)}g* | Yog: *{round(tf)}g* | Uglevod: *{round(tc)}g*\n\n"
        t2+=f"Suv: *{water}*/{wg} `{wb}`\n"
        if streak>0: t2+=f"Seriya: *{streak}* kun\n"
        if items: t2+=f"\nOvqatlar ({len(tl)}):\n{items}"
        t2+=ta
        kb=types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("AI maslahat",callback_data="ai_advice"),
               types.InlineKeyboardButton("Haftalik",callback_data="weekly"))
        bot.send_message(uid,t2,parse_mode="Markdown",reply_markup=kb)

    elif text=="Suv":
        user=get_user(uid); cur=user.get("water",{}).get(get_today(),0); wg=user.get("water_goal",8)
        bar=progress_bar(cur,wg,8)
        kb=types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("+1",callback_data="w_1"),
               types.InlineKeyboardButton("+2",callback_data="w_2"),
               types.InlineKeyboardButton("+3",callback_data="w_3"))
        kb.row(types.InlineKeyboardButton("Tiklash",callback_data="w_reset"))
        bot.send_message(uid,f"*Suv kuzatgich*\n\nBugun: *{cur}*/{wg} stakan\n`{bar}`",
            parse_mode="Markdown",reply_markup=kb)

    elif text=="Mashq":
        kb=types.InlineKeyboardMarkup()
        row=[]
        for k,v in EXERCISES.items():
            row.append(types.InlineKeyboardButton(v["name"],callback_data=f"ex_{k}"))
            if len(row)==2: kb.row(*row); row=[]
        if row: kb.row(*row)
        kb.row(types.InlineKeyboardButton("Bugungi mashqlar",callback_data="ex_today"))
        bot.send_message(uid,"*Mashq*\nQaysi mashq?",parse_mode="Markdown",reply_markup=kb)

    elif text=="Vazn":
        user=get_user(uid); wl=user.get("weight_log",{})
        last=""
        if wl: lt=sorted(wl.keys())[-1]; last=f"\nOxirgi: *{wl[lt]}* kg ({lt})"
        kb=types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("Kiritish",callback_data="wt_input"),
               types.InlineKeyboardButton("Tarix",callback_data="wt_history"))
        kb.row(types.InlineKeyboardButton("BMI",callback_data="wt_bmi"))
        bot.send_message(uid,f"*Vazn kuzatish*{last}\n\nVaznni kiriting yoki tugma bosing:",
            parse_mode="Markdown",reply_markup=kb)

    elif text=="Maqsad":
        bot.send_message(uid,"*Maqsad*\n`/goal 2000`\n\n- Yoqotish: 1500-1800\n- Saqlash: 2000-2500\n- Olish: 2800-3500",parse_mode="Markdown")

    elif text=="Sozlamalar":
        user=get_user(uid); is_p=check_premium(user)
        kb=types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton(f"Eslatma: {'On' if user.get('reminders') else 'Off'}",callback_data="s_reminder"))
        kb.row(types.InlineKeyboardButton("Sogliq holati",callback_data="s_health"))
        kb.row(types.InlineKeyboardButton("Suv normasi",callback_data="s_water_goal"))
        kb.row(types.InlineKeyboardButton("Referral",callback_data="s_referral"))
        kb.row(types.InlineKeyboardButton(f"Premium: {'Faol' if is_p else 'Yoq'}",callback_data="s_premium"))
        kb.row(types.InlineKeyboardButton("Tozalash",callback_data="s_clear"))
        bot.send_message(uid,"*Sozlamalar*",parse_mode="Markdown",reply_markup=kb)

# ===== ESLATMA =====
def reminder_loop():
    while True:
        _time.sleep(14400)  # 4 soat
        try:
            hour=datetime.now().hour; data=load_data()
            for uid,user in data.items():
                if not user.get("reminders",True): continue
                today=get_today(); tl=user.get("history",{}).get(today,[])
                water=user.get("water",{}).get(today,0)
                try:
                    if 7<=hour<=9 and not tl:
                        bot.send_message(int(uid),random.choice(MOTIVATIONS)+"\nNonushtani suratga oling!")
                    elif 12<=hour<=14:
                        bot.send_message(int(uid),"Tushlik vaqti! Ovqatingizni suratga oling.")
                    elif 18<=hour<=20:
                        if water<user.get("water_goal",8):
                            bot.send_message(int(uid),f"Suv ichishni unutmang! ({water}/{user.get('water_goal',8)})")
                except: pass
        except: pass

# ===== MAIN =====
if __name__ == "__main__":
    print("Kaloriya Hisoblagich Bot ishga tushmoqda...")
    # Eslatma threadini boshlash
    t=threading.Thread(target=reminder_loop,daemon=True)
    t.start()
    print("Bot tayyor! /start buyrug'ini yuboring.")
    bot.infinity_polling(skip_pending=True)
