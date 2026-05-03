import json


SKIP_TEXTS = [
    '本次会议已结束。', 'the meeting has ended. ',
    'Thanks for your participation. The meeting is ready to start. Please remain on the line. ',
    '请稍后。', '财经电话会议系统，', '请输入参会密码并以井号键结束。',
    '您已成功加入会议。'
]


def format_transcript(mt_summary):
    if not mt_summary or not mt_summary.get('content'):
        return None

    content_list = mt_summary['content']
    if isinstance(content_list, str):
        try:
            content_list = json.loads(content_list)
        except (json.JSONDecodeError, TypeError):
            return content_list

    if not isinstance(content_list, list):
        return str(content_list)

    speakers = mt_summary.get('speakers', [])
    role_map = {str(s.get('roleId', '')): s.get('name', f'发言人{s["roleId"]}') for s in speakers}

    lines = []
    current_role = None
    current_texts = []

    for item in content_list:
        if not isinstance(item, dict):
            continue
        text = item.get('content', '').strip()
        if not text or text in SKIP_TEXTS:
            continue

        role_id = str(item.get('role', ''))
        if role_id != current_role:
            if current_texts:
                name = role_map.get(current_role, f'发言人{current_role}')
                lines.append(f"**{name}**: {''.join(current_texts)}")
            current_role = role_id
            current_texts = [text]
        else:
            current_texts.append(text)

    if current_texts:
        name = role_map.get(current_role, f'发言人{current_role}')
        lines.append(f"**{name}**: {''.join(current_texts)}")

    return '\n\n'.join(lines)
