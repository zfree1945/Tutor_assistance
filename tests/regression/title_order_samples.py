"""用户提供的五条原样订单及人工预期；不通过解析器生成答案。"""

NOTICE = "温州大学生单\n#诚招校园小助理~高价回收家教~"
RAWS = [
    "【初三科学，鹿城区江滨西路九洲大厦】初三女生，不及格，想提高成绩，一周一次，一次两小时，周六除了下午1-3点，其他时间都可以。女大学生160两小时。S188519-2",
    "S223074【初中全科，鹿城下吕浦2区秋明组团】初3女，辅导作业，错题答疑，辅导作业巩固知识，一周五次，一次两个半小时，周一到周五19:00-21:30，大学生200/2.5h，要求女在读大学生",
    "【高中理科，龙湾万达旁边】高1女，辅导数理化试卷错题，查漏补缺，，一周一次，一次两小时，周日上午，大学生180-220/2h，要求女在读大学生",
    "S220891【初中全科，鹿城麻行小区】新初3男，基础一般，主要负责社会、科学、语文的讲解，一周五次，一次两小时，周一至周五晚18:30～20:30，大学生170两小时，要求在读大学生，经验丰富，最好医学院，理科强一些，有耐心，不报答案",
    "S218080【小学全科，鹿城八仙楼小区】新6年级女，作业全科错题答疑。数学加强难题训练（附加题）。，一周一次课，一次小时，时间协调，大学生150两小时，要求在读大学生,在读硕士生，要求师范生，擅长数学科目。",
]
EXPECTED = [
    {"address": "鹿城区江滨西路九洲大厦", "grade": "初中", "order_number": "S188519-2", "hourly_rate": 80, "weekly_sessions": 1, "lesson_hours": 2, "teacher_gender": "女", "mode": "线下", "days": [6], "time_slots": []},
    {"address": "鹿城下吕浦2区秋明组团", "grade": "初中", "order_number": "S223074", "hourly_rate": 80, "weekly_sessions": 5, "lesson_hours": 2.5, "teacher_gender": "女", "mode": "线下", "days": [1, 2, 3, 4, 5], "time_slots": ["晚上"]},
    {"address": "龙湾万达旁边", "grade": "高中", "order_number": None, "hourly_rate_min": 90, "hourly_rate_max": 110, "weekly_sessions": 1, "lesson_hours": 2, "teacher_gender": "女", "mode": "线下", "days": [7], "time_slots": ["上午"]},
    {"address": "鹿城麻行小区", "grade": "初中", "order_number": "S220891", "hourly_rate": 85, "weekly_sessions": 5, "lesson_hours": 2, "teacher_gender": "不限", "mode": "线下", "days": [1, 2, 3, 4, 5], "time_slots": ["晚上"]},
    {"address": "鹿城八仙楼小区", "grade": "小学", "order_number": "S218080", "hourly_rate": 75, "weekly_sessions": 1, "lesson_hours": None, "teacher_gender": "不限", "mode": "线下", "days": [], "time_slots": []},
]
BATCH = NOTICE + "\n\n" + "\n\n".join(RAWS)
