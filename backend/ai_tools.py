import os
import google.generativeai as genai
from flask import Blueprint, request, Response, jsonify

# --- 初始化 AI Blueprint ---
ai_bp = Blueprint('ai_bp', __name__)

# --- 初始化 Google GenAI ---

# 從環境變數中讀取 API Key (這會由主 app.py 載入)
api_key = os.getenv('GEMINI_API_KEY')

# 檢查 API Key 是否存在
if not api_key:
  print("AI 模組警告：在環境變數中找不到 GEMINI_API_KEY。")
  model = None # 將 model 設為 None
else:
  # 設定 Google Gen AI
  genai.configure(api_key=api_key)
  # 初始化模型
  model = genai.GenerativeModel('gemini-2.5-flash')


# ==========================================================
# AI 處理的 API 端點
# ==========================================================
@ai_bp.route('/api/revise-resume', methods=['POST'])
def revise_resume():
    
    # 檢查 API Key 是否在啟動時成功載入
    if not api_key or not model:
        return jsonify({"error": "AI 服務未正確配置 API Key。"}), 500

    # 接收履歷文本、任務風格、語氣風格
    try:
        data = request.get_json()
        user_resume_text = data.get('resumeText')
        edit_style = data.get('style', 'polish')
        tone_style = data.get('tone', 'professional')

        if not user_resume_text:
            return jsonify({"error": "請提供履歷文本。"}), 400

    except Exception as e:
        print(f"請求解析錯誤: {e}")
        return jsonify({"error": "無效的請求格式。"}), 400

    try:
        final_prompt = ""
        
        # --- 步驟一：定義語氣風格 (Tone) ---
        
        if tone_style == 'friendly':
            tone_prompt = "語氣必須親切隨和。"
        
        # 'creative' (活潑有創意) 已被移除

        elif tone_style == 'cautious':
            tone_prompt = "語氣必須專業、謹慎且精確。"
        
        elif tone_style == 'academic':
            tone_prompt = "語氣必須嚴謹、客觀且具學術性。"
            
        else:
            # 預設 ('professional') 語氣的專業強化 (針對履歷情境)
            tone_prompt = "語氣必須專業正式且符合商業履歷標準。規則：1. 避免個人感悟、心態或哲學性描述。2. 強調具體行動和成就。"


        # --- 步驟二：定義主要任務 (Task) ---
        
        if edit_style == 'keyword_focus':
            # --- 選項 1: 關鍵字導向 (兩步驟) ---
            keyword_prompt = f"[任務] 從以下履歷文本中提取 5-7 個最核心的技能和成就關鍵字。[規則] 以逗號 (,) 分隔所有關鍵字，並在**一行中**輸出。[原始文本] {user_resume_text} [關鍵字列表]"
            keyword_response = model.generate_content(keyword_prompt)
            keywords = keyword_response.text.strip()
            print(f"偵測任務: 關鍵字導向 (關鍵字: {keywords}), 語氣: {tone_style}")

            final_prompt = f"[任務] 你是一位頂尖的人力資源專家。請根據 [核心關鍵字] 重寫 [原始文本]。[關鍵規則] 1. **必須**突出並強調 [核心關鍵字] 相關的技能和成就。 2. **{tone_prompt}** [規則] 1. 使用強動詞開頭的行動句。 2. 量化成果。 3. 禁止包含任何原始文本之外的解釋或評論。[核心關鍵字] {keywords} [原始文本] {user_resume_text} [修改後的文本]"
        
        elif edit_style == 'concise':
            # --- 選項 2: 文案精簡 (一步驟) ---
            # 強化文案精簡任務，強制其以成就導向
            print(f"偵測任務: 文案精簡, 語氣: {tone_style}")
            final_prompt = f"[任務] 將以下 [原始文本] 改寫得**極度精簡、清楚明瞭且成就導向**。[規則] 1. **{tone_prompt}** 2. **每句話必須以行動動詞開頭**。 3. 刪除所有贅字、口語化和非成就型描述。 4. 保留並強化核心資訊。 5. 禁止包含任何原始文本之外的解釋或評論。[原始文本] {user_resume_text} [修改後的文本]"

        else: # 'polish' (預設)
            # --- 選項 3: 履歷美化 (預設) (一步驟) ---
            print(f"偵測任務: 履歷美化, 語氣: {tone_style}")
            # 修正原始程式碼中 tone_prompt 的引用錯誤 ($ 改為 {})
            final_prompt = f"[任務] 專業地**美化並潤飾**以下 [原始文本]。[規則] 1. **{tone_prompt}** 2. 使用強動詞開頭的行動句。 3. 盡可能量化成果。 4. 修正文法。 5. 禁止包含任何原始文本之外的解釋或評論。[原始文本] {user_resume_text} [修改後的文本]"

        # --- 統一的串流輸出 ---
        
        def generate_stream():
            try:
                response_stream = model.generate_content(final_prompt, stream=True)
                for chunk in response_stream:
                    if chunk.text:
                        yield chunk.text
            except Exception as e:
                print(f"串流處理中發生錯誤: {e}")
                yield f"AI 服務處理失敗: {e}"

        headers = {
            'Content-Type': 'text/plain; charset=utf-8',
            'Transfer-Encoding': 'chunked',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        }
        return Response(generate_stream(), headers=headers)

    except Exception as e:
        print(f"Gemini API 呼叫失敗： {e}")
        return jsonify({"error": f"AI 服務處理失敗: {e}"}), 500