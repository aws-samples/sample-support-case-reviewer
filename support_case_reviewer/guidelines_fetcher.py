import json
from typing import List, Optional, Dict, Any
import re

import httpx
from bs4 import BeautifulSoup, Tag


class Constants:
    DEFAULT_TIMEOUT = 30
    GUIDELINES_URL = "https://aws.amazon.com/jp/premiumsupport/tech-support-guidelines/"
    MIN_ITEMS_COUNT = 10
    LINEBREAK_MARKER = "|||LINEBREAK|||"
    MARKDOWN_LINE_BREAK = "  \n"
    SECTION_SEPARATOR = "\n\n"
    ERROR_MESSAGE = "予期せぬエラーによりガイドラインの内容を取得できませんでした"


class CompiledRegex:
    """コンパイル済み正規表現パターン"""
    BOLD_TAG = re.compile(r'<b>(.*?)</b>', re.DOTALL)
    BR_TAG = re.compile(r'<br\s*/?>', re.IGNORECASE)
    HTML_TAG = re.compile(r'<[^>]+>')


class HTMLToMarkdownConverter:
    def convert_html_to_markdown(self, html_content: str) -> str:
        """HTML文字列をMarkdown形式に変換"""
        if not html_content or not html_content.strip():
            return ""
        
        soup = BeautifulSoup(html_content, 'html.parser')
        markdown_lines = []
        
        for element in soup.children:
            if hasattr(element, 'name') and element.name:
                converted = self._convert_element(element)
                if converted:
                    markdown_lines.append(converted)
            elif hasattr(element, 'strip'):
                text = element.strip()
                if text:
                    markdown_lines.append(text)
        
        return '\n'.join(markdown_lines)
    
    def _convert_element(self, element: Tag) -> str:
        """HTML要素をMarkdown形式に変換"""
        # タグ名に対応する処理関数のディスパッチ辞書
        dispatch = {
            'ul': self._convert_unordered_list,
            'ol': self._convert_ordered_list,
            'table': self._convert_table,
            'p': lambda e: e.get_text(strip=True)
        }
        
        # 対応する処理関数を取得、なければデフォルト処理
        converter_func = dispatch.get(element.name, lambda e: e.get_text(strip=True))
        return converter_func(element)
    
    def _convert_unordered_list(self, ul_element: Tag) -> str:
        """ul要素をMarkdown形式に変換"""
        return self._convert_list(ul_element, lambda i, text: f"- {text}")
    
    def _convert_ordered_list(self, ol_element: Tag) -> str:
        """ol要素をMarkdown形式に変換"""
        return self._convert_list(ol_element, lambda i, text: f"{i+1}. {text}")
    
    def _convert_list(self, list_element: Tag, formatter) -> str:
        """リスト要素をMarkdown形式に変換（共通処理）"""
        lines = []
        for i, li in enumerate(list_element.find_all('li', recursive=False)):
            li_text = self._process_li_content(li)
            if li_text:
                lines.append(formatter(i, li_text))
        return '\n'.join(lines)
    
    def _convert_table(self, table_element: Tag) -> str:
        """table要素をMarkdown形式に変換"""
        rows = table_element.find_all('tr')
        if not rows:
            return ''
        
        lines = []
        
        # ヘッダー行の処理
        header_row = rows[0]
        headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]
        if headers:
            lines.append('| ' + ' | '.join(headers) + ' |')
            lines.append('| ' + ' | '.join(['---'] * len(headers)) + ' |')
        
        # データ行の処理
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
            if cells:
                lines.append('| ' + ' | '.join(cells) + ' |')
        
        return '\n'.join(lines)
    
    def _process_li_content(self, li_element: Tag) -> str:
        """li要素の内容を処理し、brタグで改行、bタグを太字にする"""
        # HTMLを文字列として取得し、コンパイル済み正規表現で置換
        html_str = str(li_element)
        
        # bタグを太字（**）に置換
        html_str = CompiledRegex.BOLD_TAG.sub(r'**\1**', html_str)
        
        # brタグを改行マーカーに置換
        html_str = CompiledRegex.BR_TAG.sub(Constants.LINEBREAK_MARKER, html_str)
        
        # HTMLタグを除去
        text = CompiledRegex.HTML_TAG.sub('', html_str).strip()
        
        # 改行マーカーを実際の改行に置換
        lines = [line.strip() for line in text.split(Constants.LINEBREAK_MARKER) if line.strip()]
        
        # Markdownの改行ルールに従って半角スペース2つで結合
        return Constants.MARKDOWN_LINE_BREAK.join(lines)


class JSONDataProcessor:
    def __init__(self, converter: HTMLToMarkdownConverter):
        self.converter = converter
    
    def extract_json_data(self, content: BeautifulSoup) -> Optional[List[Dict[str, Any]]]:
        """JSONデータからガイドライン情報を抽出"""
        script_tags = content.find_all('script', type='application/json')
        
        for script in script_tags:
            if not script.string:
                continue
                
            try:
                data = json.loads(script.string)
                items = self._extract_guideline_items_from_json(data)
                if items:
                    return items
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
                
        return None
    
    def _extract_guideline_items_from_json(self, data: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """JSONデータからアイテムリストを抽出"""
        if not isinstance(data, dict) or 'data' not in data:
            return None
            
        data_section = data['data']
        if not isinstance(data_section, dict) or 'items' not in data_section:
            return None
            
        items = data_section['items']
        if not isinstance(items, list) or len(items) < Constants.MIN_ITEMS_COUNT:
            return None
            
        # 最初のアイテムが有効な構造を持っているかチェック
        if items and isinstance(items[0], dict) and 'fields' in items[0]:
            return items
            
        return None
    
    def process_json_data(self, items: List[Dict[str, Any]]) -> str:
        """JSONデータからMarkdown形式のガイドラインを生成"""
        sections = []
        current_category = None
        
        for item in items:
            if not self._is_valid_item(item):
                continue
            
            # カテゴリの処理
            category = self._extract_category(item)
            if category and category != current_category:
                current_category = category
                sections.append(f"## {category}")
            
            # アイテムの処理
            item_sections = self._convert_item_to_markdown_sections(item)
            sections.extend(item_sections)
        
        return Constants.SECTION_SEPARATOR.join(sections)
    
    def _is_valid_item(self, item: Dict[str, Any]) -> bool:
        """アイテムが有効かどうかをチェック"""
        if 'fields' not in item:
            return False
        
        heading = item['fields'].get('itemHeading', '')
        return bool(heading and heading != 'NA')
    
    def _extract_category(self, item: Dict[str, Any]) -> Optional[str]:
        """アイテムからカテゴリを抽出"""
        metadata = item.get('metadata', {})
        if not isinstance(metadata, dict):
            return None
            
        tags = metadata.get('tags', [])
        if not isinstance(tags, list) or not tags:
            return None
            
        first_tag = tags[0]
        if not isinstance(first_tag, dict):
            return None
            
        name = first_tag.get('name', '')
        return name if name else None
    
    def _convert_item_to_markdown_sections(self, item: Dict[str, Any]) -> List[str]:
        """個別アイテムを処理してMarkdownセクションを生成"""
        sections = []
        fields = item['fields']
        
        # 見出しの追加
        heading = fields.get('itemHeading', '')
        sections.append(f"### {heading}")
        
        # 内容の処理
        content = fields.get('itemLongLoc', '')
        if content:
            markdown_content = self.converter.convert_html_to_markdown(content)
            # strip()の結果を再利用
            stripped_content = markdown_content.strip()
            if stripped_content:
                sections.append(stripped_content)
        
        return sections


class GuidelinesFetcher:
    def __init__(self, timeout: int = Constants.DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.guidelines_url = Constants.GUIDELINES_URL
        
        # 依存関係の注入
        self.converter = HTMLToMarkdownConverter()
        self.json_processor = JSONDataProcessor(self.converter)
    
    async def get_guidelines(self) -> str:
        try:
            html_content = await self._fetch_from_url()
            result = self._parse_content(html_content)
            
            # 空文字または無効な結果の場合はエラーとして扱う
            if not result or not result.strip():
                return Constants.ERROR_MESSAGE
            
            return result
        except Exception:
            # すべての例外をキャッチしてエラーメッセージを返す
            return Constants.ERROR_MESSAGE
    
    async def _fetch_from_url(self) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(self.guidelines_url)
            response.raise_for_status()
            return response.text
    
    def _parse_content(self, html: str) -> str:
        if not html or not html.strip():
            return ""
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # JSONデータから構造化されたコンテンツを抽出
        json_data = self.json_processor.extract_json_data(soup)
        if json_data:
            return self.json_processor.process_json_data(json_data)
        
        # フォールバック: 全体のテキストを返す
        return self._extract_all_text(soup)
    
    def _extract_all_text(self, content: BeautifulSoup) -> str:
        text = content.get_text(separator='\n', strip=True)
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return '\n'.join(lines)