"""五条虚构地点及编号的脱敏订单及人工预期；不通过解析器生成答案。"""

NOTICE = "温州大学生单\n#诚招校园小助理~高价回收家教~"
RAWS = [
    "【初三科学，鹿城区合成西路示例大厦】初三女生，不及格，想提高成绩，一周一次，一次两小时，周六除了下午1-3点，其他时间都可以。女大学生160两小时。S411842-5",
    "S556307【初中全科，鹿城合成社区2区示例组团】初3女，辅导作业，错题答疑，辅导作业巩固知识，一周五次，一次两个半小时，周一到周五19:00-21:30，大学生200/2.5h，要求女在读大学生",
    "【高中理科，龙湾合成广场旁边】高1女，辅导数理化试卷错题，查漏补缺，，一周一次，一次两小时，周日上午，大学生180-220/2h，要求女在读大学生",
    "S553124【初中全科，鹿城示范小区】新初3男，基础一般，主要负责社会、科学、语文的讲解，一周五次，一次两小时，周一至周五晚18:30～20:30，大学生170两小时，要求在读大学生，经验丰富，最好医学院，理科强一些，有耐心，不报答案",
    "S541313【小学全科，鹿城合成楼小区】新6年级女，作业全科错题答疑。数学加强难题训练（附加题）。，一周一次课，一次小时，时间协调，大学生150两小时，要求在读大学生,在读硕士生，要求师范生，擅长数学科目。",
]
EXPECTED = [
    {"address": "鹿城区合成西路示例大厦", "grade": "初中", "order_number": "S411842-5", "hourly_rate": 80, "weekly_sessions": 1, "lesson_hours": 2, "teacher_gender": "女", "mode": "线下", "days": [6], "time_slots": []},
    {"address": "鹿城合成社区2区示例组团", "grade": "初中", "order_number": "S556307", "hourly_rate": 80, "weekly_sessions": 5, "lesson_hours": 2.5, "teacher_gender": "女", "mode": "线下", "days": [1, 2, 3, 4, 5], "time_slots": ["晚上"]},
    {"address": "龙湾合成广场旁边", "grade": "高中", "order_number": None, "hourly_rate_min": 90, "hourly_rate_max": 110, "weekly_sessions": 1, "lesson_hours": 2, "teacher_gender": "女", "mode": "线下", "days": [7], "time_slots": ["上午"]},
    {"address": "鹿城示范小区", "grade": "初中", "order_number": "S553124", "hourly_rate": 85, "weekly_sessions": 5, "lesson_hours": 2, "teacher_gender": "不限", "mode": "线下", "days": [1, 2, 3, 4, 5], "time_slots": ["晚上"]},
    {"address": "鹿城合成楼小区", "grade": "小学", "order_number": "S541313", "hourly_rate": 75, "weekly_sessions": 1, "lesson_hours": None, "teacher_gender": "不限", "mode": "线下", "days": [], "time_slots": []},
]
BATCH = NOTICE + "\n\n" + "\n\n".join(RAWS)
