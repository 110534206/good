from flask import Blueprint, request, jsonify, session, send_file, render_template
from werkzeug.utils import secure_filename
import os
vendor_bp = Blueprint('vendor', __name__)


@vendor_bp.route('/vendor/resume-review')
def vendor_resume_review():
    return render_template('user_shared/review_resumes.html')