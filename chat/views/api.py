import logging
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

# This file is intentionally left empty.
# The original HTTP-based API views have been deprecated and replaced by
# WebSocket consumers (consumers.py) for core chat functionality
# and admin-specific views in admin_api.py.

# The is_user_admin helper function has been moved to admin_api.py.
