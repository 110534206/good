from flask import Blueprint, request, jsonify, session, send_file, render_template
from werkzeug.utils import secure_filename
import os
vendor_bp = Blueprint('vendor', __name__)