"""
测试 Word 模板生成功能
用于验证模板配置是否正确
"""

import os
import sys
from docxtpl import DocxTemplate

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(__file__))

def test_template():
    """测试模板生成"""
    try:
        # 模板路径
        base_dir = os.path.dirname(__file__)
        template_path = os.path.join(base_dir, "..", "frontend", "static", "examples", "實習履歷(空白).docx")
        template_path = os.path.abspath(template_path)
        
        print(f"📄 模板路径: {template_path}")
        
        if not os.path.exists(template_path):
            print(f"❌ 模板文件不存在: {template_path}")
            return False
        
        # 加载模板
        doc = DocxTemplate(template_path)
        
        # 测试数据
        context = {
            'StuID': '110534235',
            'StuName': '测试学生',
            'BirthDate': '2000-01-01',
            'Gender': '男',
            'Phone': '0912345678',
            'Email': 'test@example.com',
            'Address': '测试地址123号',
            'ConductScore': '甲',
            'Autobiography': '这是测试自传内容。',
            
            'courses': [
                {'name': '程式設計', 'credits': '3', 'grade': '85'},
                {'name': '資料庫管理', 'credits': '2', 'grade': '90'},
                {'name': '網頁設計', 'credits': '2', 'grade': '88'},
            ],
            
            'certificates': [
                {'type': '證照', 'name': '電腦軟體應用', 'proficiency': '乙級'},
                {'type': '語文', 'name': '英文', 'proficiency': '中級'},
            ],
            
            'preferences': [
                {'rank': '1', 'company': '测试公司A', 'job_title': '軟體開發工程師'},
                {'rank': '2', 'company': '测试公司B', 'job_title': '系統分析師'},
            ],
        }
        
        # 填充模板
        doc.render(context)
        
        # 保存测试文件
        output_path = os.path.join(base_dir, "test_output.docx")
        doc.save(output_path)
        
        print(f"✅ 测试文件已生成: {output_path}")
        print("\n请检查生成的Word文件，确认所有字段都已正确填充。")
        print("\n如果字段没有填充，请检查Word模板中是否使用了正确的变量名：")
        print("  - {{StuID}}, {{StuName}}, {{BirthDate}} 等")
        print("  - 表格循环需要使用 {%tr for course in courses %} 语法")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_template()

