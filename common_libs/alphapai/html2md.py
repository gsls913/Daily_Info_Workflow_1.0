import re


def html_to_markdown(html_content):
    if not html_content:
        return None

    text = html_content.strip()

    text = re.sub(r'<h1[^>]*>.*?</h1>', '', text, count=1, flags=re.DOTALL)
    text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', text, flags=re.DOTALL)
    text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', text, flags=re.DOTALL)
    text = re.sub(r'<p[^>]*>', '\n', text)
    text = re.sub(r'</p>', '\n', text)

    def find_matching_close(text, start, open_tag, close_tag):
        depth = 1
        pos = start
        open_len = len(open_tag)
        close_len = len(close_tag)
        while depth > 0 and pos < len(text):
            no = text.find(open_tag, pos)
            nc = text.find(close_tag, pos)
            if nc == -1:
                return -1
            if no != -1 and no < nc:
                depth += 1
                pos = no + open_len
            else:
                depth -= 1
                if depth == 0:
                    return nc
                pos = nc + close_len
        return -1

    def clean_inline_html(text):
        if not text:
            return ''
        text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)
        text = (text.replace('&lt;', '<').replace('&gt;', '>')
                    .replace('&amp;', '&').replace('&quot;', '"')
                    .replace('&#39;', "'").replace('&nbsp;', ' '))
        return text.strip()

    def parse_html_recursive(html, depth=0):
        result = []
        i = 0

        while i < len(html):
            ul_start = html.find('<ul', i)

            if ul_start == -1:
                remaining = html[i:].strip()
                if remaining:
                    remaining = clean_inline_html(remaining)
                    if remaining:
                        result.append(remaining)
                break

            if ul_start > i:
                before = html[i:ul_start].strip()
                if before:
                    before = clean_inline_html(before)
                    if before:
                        result.append(before)

            ul_tag_end = html.find('>', ul_start)
            ul_content_start = ul_tag_end + 1
            ul_end = find_matching_close(html, ul_content_start, '<ul', '</ul')

            if ul_end == -1:
                break

            ul_content = html[ul_content_start:ul_end]

            li_pos = 0
            while li_pos < len(ul_content):
                li_start = ul_content.find('<li', li_pos)
                if li_start == -1:
                    break

                li_tag_end = ul_content.find('>', li_start)
                li_content_start = li_tag_end + 1
                li_end = find_matching_close(ul_content, li_content_start, '<li', '</li')

                if li_end == -1:
                    break

                li_body = ul_content[li_content_start:li_end]

                nested_ul_start = li_body.find('<ul')

                if nested_ul_start != -1:
                    li_text = li_body[:nested_ul_start].strip()
                    li_text = clean_inline_html(li_text)

                    nested_ul_tag_end = li_body.find('>', nested_ul_start)
                    nested_ul_content_start = nested_ul_tag_end + 1
                    nested_ul_end = find_matching_close(li_body, nested_ul_content_start, '<ul', '</ul')

                    if nested_ul_end != -1:
                        nested_ul_html = li_body[nested_ul_start:nested_ul_end + 5]

                        if li_text:
                            indent = '\t' * depth
                            result.append(f'{indent}- {li_text}')

                        nested_md, _ = parse_html_recursive(nested_ul_html, depth + 1)
                        if nested_md:
                            result.append(nested_md)
                else:
                    li_text = clean_inline_html(li_body)
                    if li_text:
                        indent = '\t' * depth
                        result.append(f'{indent}- {li_text}')

                li_pos = li_end + 5

            i = ul_end + 5

        return '\n'.join(result), html[i:]

    text, _ = parse_html_recursive(text, 0)

    text = re.sub(r'<[^>]+>', '', text)

    text = (text.replace('&lt;', '<').replace('&gt;', '>')
                .replace('&amp;', '&').replace('&quot;', '"')
                .replace('&#39;', "'").replace('&nbsp;', ' '))

    text = re.sub(r'\n{3,}', '\n\n', text)

    text = re.sub(r'\*\*Q:\*\*(?! )', '**Q:** ', text)
    text = re.sub(r'\*\*A:\*\*(?! )', '**A:** ', text)

    return text.strip() or None
