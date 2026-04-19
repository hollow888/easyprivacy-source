import os
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, session, send_from_directory
from sqlalchemy.exc import SQLAlchemyError
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import markdown
import bleach



# GitHub integration
try:
    from github import Github, Auth
    from github.GithubException import GithubException
    GITHUB_AVAILABLE = True
    print("✅ GitHub module loaded")
except ImportError:
    GITHUB_AVAILABLE = False
    print("⚠️ GitHub module not available - install with: pip install PyGithub")

# Load environment variables
load_dotenv()

# App config

app = Flask(__name__)

@app.route('/', methods=['GET'])
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/style.css', methods=['GET'])
def serve_style():
    return send_from_directory('.', 'style.css')

@app.route('/favicon.ico', methods=['GET'])
def favicon():
    return '', 204

# CORS configuration
CORS(app, supports_credentials=True)

# Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://postgres:password@localhost/easyprivacy')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True



# Sanitisation

ALLOWED_MARKDOWN_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    'p', 'pre', 'code', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'hr', 'br', 'span', 'img', 'table', 'thead',
    'tbody', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'strong', 'em'
]

ALLOWED_MARKDOWN_ATTRIBUTES = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    'a': ['href', 'title', 'rel', 'target'],
    'img': ['src', 'alt', 'title'],
    'code': ['class'],
    'span': ['class']
}

ALLOWED_MARKDOWN_PROTOCOLS = list(bleach.sanitizer.ALLOWED_PROTOCOLS) + ['data']

def sanitize_markdown(raw_markdown):
    """Render markdown to safe HTML for frontend display."""
    if not raw_markdown:
        return ''

    rendered = markdown.markdown(
        raw_markdown,
        extensions=['extra', 'nl2br', 'sane_lists', 'tables', 'fenced_code']
    )

    return bleach.clean(
        rendered,
        tags=ALLOWED_MARKDOWN_TAGS,
        attributes=ALLOWED_MARKDOWN_ATTRIBUTES,
        protocols=ALLOWED_MARKDOWN_PROTOCOLS,
        strip=True
    )


def get_request_data():
    """Return JSON body as a dict, or an empty dict for invalid/missing payloads."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def safe_uuid(value):
    """Parse a UUID safely, returning None for invalid values."""
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None

# Initialize database
from models import db, User, ForumCategory, ForumPost, Comment, Tag, PostTags, Vote, WikiEdit, WikiEditVote, ModerationAction, RateLimit
db.init_app(app)

# Github config

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
GITHUB_OWNER = os.getenv('GITHUB_OWNER', 'hollow888')
GITHUB_REPO = os.getenv('GITHUB_REPO', 'easy-privacy-articles')
WIKI_PATH = os.getenv('WIKI_PATH', '')

class GitHubWikiManager:
    def __init__(self, token, owner, repo_name):
        self.token = token
        self.owner = owner
        self.repo_name = repo_name
        self.wiki_path = WIKI_PATH
        self.authenticated = False
        self.repo = None
        self.g = None
        
        try:
            self.auth = Auth.Token(token)
            self.g = Github(auth=self.auth)
            user = self.g.get_user()
            print(f"✅ GitHub authenticated as: {user.login}")
            self.repo = self.g.get_repo(f"{owner}/{repo_name}")
            print(f"✅ Connected to repository: {owner}/{repo_name}")
            self.authenticated = True
        except Exception as e:
            print(f"❌ GitHub initialization failed: {e}")
            self.authenticated = False
    
    def __del__(self):
        if hasattr(self, 'g') and self.g:
            self.g.close()
    
    def get_article(self, slug):
        if not self.authenticated:
            return None
        try:
            filename = slug if slug.endswith('.md') else f"{slug}.md"
            file_path = f"{self.wiki_path}/{filename}" if self.wiki_path else filename
            contents = self.repo.get_contents(file_path)
            content = contents.decoded_content.decode('utf-8')
            return {
                'content': content,
                'sha': contents.sha,
                'path': file_path,
                'html_url': contents.html_url,
                'last_modified': contents.last_modified or datetime.now().isoformat()
            }
        except GithubException as e:
            if e.status == 404:
                return None
            print(f"Error getting article {slug}: {e}")
            return None
    
    def get_article_list(self):
        if not self.authenticated:
            return []
        try:
            contents = self.repo.get_contents(self.wiki_path if self.wiki_path else "")
            articles = []
            for content in contents:
                if content.name.endswith('.md'):
                    articles.append({
                        'name': content.name.replace('.md', ''),
                        'path': content.path,
                        'url': content.html_url
                    })
            return articles
        except GithubException as e:
            print(f"Error getting article list: {e}")
            return []
    
    def create_edit_branch(self, base_branch='main', branch_name=None):
        if not self.authenticated:
            return None
        try:
            if not branch_name:
                branch_name = f"edit-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            source = self.repo.get_branch(base_branch)
            self.repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=source.commit.sha)
            return branch_name
        except GithubException as e:
            print(f"Error creating branch: {e}")
            return None
    
    def submit_edit(self, slug, content, edit_comment, branch_name, author_name='Anonymous'):
        if not self.authenticated:
            return False
        try:
            file_path = f"{self.wiki_path}/{slug}.md" if self.wiki_path else f"{slug}.md"
            try:
                contents = self.repo.get_contents(file_path, ref=branch_name)
                self.repo.update_file(
                    path=file_path,
                    message=edit_comment,
                    content=content,
                    sha=contents.sha,
                    branch=branch_name
                )
            except GithubException:
                self.repo.create_file(
                    path=file_path,
                    message=edit_comment,
                    content=content,
                    branch=branch_name
                )
            return True
        except GithubException as e:
            print(f"Error submitting edit: {e}")
            return False
    
    def create_pull_request(self, branch_name, title, body):
        if not self.authenticated:
            return None
        try:
            pr = self.repo.create_pull(
                title=title,
                body=body,
                head=branch_name,
                base='main'
            )
            return pr
        except GithubException as e:
            print(f"Error creating PR: {e}")
            return None
    
    def merge_pull_request(self, pr_number):
        if not self.authenticated:
            return False
        try:
            pr = self.repo.get_pull(pr_number)
            if pr.mergeable:
                pr.merge()
                return True
            return False
        except GithubException as e:
            print(f"Error merging PR: {e}")
            return False

# Initialize GitHub manager
github_manager = None
if GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO and GITHUB_AVAILABLE:
    github_manager = GitHubWikiManager(GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO)

# Auth decorators

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'Authentication required'}), 401
        user_uuid = safe_uuid(user_id)
        if not user_uuid or not db.session.get(User, user_uuid):
            session.clear()
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def moderator_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        user = db.session.get(User, safe_uuid(session.get('user_id')))
        if not user or not (user.is_moderator or user.is_admin):
            return jsonify({'error': 'Moderator privileges required'}), 403
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        user = db.session.get(User, safe_uuid(session.get('user_id')))
        if not user or not user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403
        return f(*args, **kwargs)
    return decorated_function

def contributor_or_moderator_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        user = db.session.get(User, safe_uuid(session.get('user_id')))
        if not user or not (user.is_contributor or user.is_moderator or user.is_admin):
            return jsonify({'error': 'Contributor privileges required'}), 403
        return f(*args, **kwargs)
    return decorated_function

# helper functions

def get_current_user():
    user_id = session.get('user_id')
    user_uuid = safe_uuid(user_id)
    if not user_uuid:
        return None
    return db.session.get(User, user_uuid)

def check_rate_limit(action_type, max_actions=10, window_minutes=5):
    user = get_current_user()
    if not user:
        return True
    
    cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
    
    RateLimit.query.filter(
        RateLimit.user_id == user.id,
        RateLimit.first_action_at < cutoff
    ).delete()
    db.session.commit()
    
    rate_limit = RateLimit.query.filter_by(
        user_id=user.id,
        action_type=action_type
    ).first()
    
    if rate_limit:
        if rate_limit.action_count >= max_actions:
            return False
        rate_limit.action_count += 1
        rate_limit.last_action_at = datetime.utcnow()
    else:
        rate_limit = RateLimit(
            user_id=user.id,
            action_type=action_type,
            action_count=1,
            first_action_at=datetime.utcnow(),
            last_action_at=datetime.utcnow()
        )
        db.session.add(rate_limit)
    
    db.session.commit()
    return True

# Auth routes

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = get_request_data()
    
    if not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Username and password required'}), 400
    
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Username already taken'}), 400
    
    if data.get('email'):
        if User.query.filter_by(email=data['email']).first():
            return jsonify({'error': 'Email already registered'}), 400
    else:
        data['email'] = None
    
    user = User(
        id=uuid.uuid4(),
        username=data['username'],
        email=data.get('email'),
        password_hash=generate_password_hash(data['password']),
        created_at=datetime.utcnow()
    )
    
    db.session.add(user)
    db.session.commit()
    
    session['user_id'] = str(user.id)
    session.permanent = True
    
    return jsonify({
        'message': 'Registration successful',
        'user': user.to_dict()
    })

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = get_request_data()
    
    if not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Username/email and password required'}), 400
    
    user = User.query.filter(
        (User.username == data['username']) | 
        (User.email == data['username'])
    ).first()
    
    if not user or not check_password_hash(user.password_hash, data['password']):
        return jsonify({'error': 'Invalid username/email or password'}), 401
    
    user.last_seen_at = datetime.utcnow()
    db.session.commit()
    
    session['user_id'] = str(user.id)
    session.permanent = True
    
    return jsonify({
        'message': 'Login successful',
        'user': user.to_dict()
    })

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Logout successful'})

@app.route('/api/auth/me', methods=['GET'])
def get_current_user_info():
    user = get_current_user()
    return jsonify({'user': user.to_dict() if user else None})

@app.route('/api/auth/me', methods=['OPTIONS'])
def handle_auth_me_options():
    return '', 200

@app.route('/api/auth/change-password', methods=['POST'])
@login_required
def change_password():
    user = get_current_user()
    data = get_request_data()
    
    if not check_password_hash(user.password_hash, data['current_password']):
        return jsonify({'error': 'Current password is incorrect'}), 401
    
    user.password_hash = generate_password_hash(data['new_password'])
    db.session.commit()
    
    return jsonify({'message': 'Password changed successfully'})

@app.route('/api/auth/add-email', methods=['POST'])
@login_required
def add_email():
    user = get_current_user()
    data = get_request_data()
    
    if not data.get('email'):
        return jsonify({'error': 'Email required'}), 400
    
    existing = User.query.filter_by(email=data['email']).first()
    if existing and existing.id != user.id:
        return jsonify({'error': 'Email already registered'}), 400
    
    user.email = data['email']
    db.session.commit()
    
    return jsonify({
        'message': 'Email added successfully',
        'user': user.to_dict()
    })


@app.route('/api/auth/update-role', methods=['POST'])
@login_required
def update_role():
    return jsonify({'error': 'Role changes are managed through the moderation dashboard'}), 403

# Forum category routes

@app.route('/api/forum/categories', methods=['GET'])
def get_categories():
    categories = ForumCategory.query.all()
    return jsonify([cat.to_dict() for cat in categories])

# Forum post routes

@app.route('/api/forum/posts', methods=['GET'])
def get_posts():
    try:
        category = request.args.get('category')
        tag = request.args.get('tag')
        sort = request.args.get('sort', 'latest')
        
        query = ForumPost.query.filter_by(is_hidden=False)
        
        if category:
            query = query.filter_by(category_id=category)
        
        if tag:
            query = query.join(PostTags).join(Tag).filter(Tag.slug == tag)
        
        if sort == 'latest':
            query = query.order_by(ForumPost.last_activity_at.desc())
        
        posts = query.limit(50).all()
        
        return jsonify([post.to_dict() for post in posts])
    except Exception as e:
        print(f"Error in get_posts: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/forum/posts', methods=['POST'])
@login_required
def create_post():
    if not check_rate_limit('create_post'):
        return jsonify({'error': 'Rate limit exceeded'}), 429
    
    data = get_request_data()
    user = get_current_user()

    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    category_id = data.get('category_id', 1)

    if not title or not content:
        return jsonify({'error': 'Title and content are required'}), 400
    
    post = ForumPost(
        id=uuid.uuid4(),
        title=title,
        content=content,
        user_id=user.id,
        category_id=category_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        last_activity_at=datetime.utcnow()
    )
    
    db.session.add(post)
    
    if 'tags' in data and data['tags']:
        for tag_name in data['tags']:
            tag = Tag.query.filter_by(name=tag_name).first()
            if not tag:
                tag = Tag(name=tag_name, slug=tag_name.lower().replace(' ', '-'))
                db.session.add(tag)
                db.session.flush()
            
            post_tag = PostTags(post_id=post.id, tag_id=tag.id)
            db.session.add(post_tag)
            tag.usage_count += 1
    
    category = ForumCategory.query.get(data.get('category_id', 1))
    if category:
        category.post_count += 1
    
    db.session.commit()
    
    return jsonify({'id': str(post.id), 'message': 'Post created successfully'})

@app.route('/api/forum/posts/<post_id>', methods=['GET'])
def get_post(post_id):
    try:
        post_uuid = safe_uuid(post_id)
        if not post_uuid:
            return jsonify({'error': 'Invalid post id'}), 400
        post = db.session.get(ForumPost, post_uuid)
        
        if not post or post.is_hidden:
            return jsonify({'error': 'Post not found'}), 404
        
        post.view_count = (post.view_count or 0) + 1
        db.session.commit()
        
        comments = Comment.query.filter_by(
            post_id=post.id,
            parent_comment_id=None,
            is_hidden=False,
            is_deleted=False
        ).order_by(Comment.created_at).all()
        
        post_dict = post.to_dict()
        post_dict['comments'] = [comment.to_dict() for comment in comments]
        
        return jsonify(post_dict)
    except Exception as e:
        print(f"Error in get_post: {e}")
        return jsonify({'error': str(e)}), 500

# Content routes

@app.route('/api/forum/posts/<post_id>/comments', methods=['POST'])
@login_required
def add_comment(post_id):
    if not check_rate_limit('add_comment'):
        return jsonify({'error': 'Rate limit exceeded'}), 429
    
    data = get_request_data()
    user = get_current_user()

    post_uuid = safe_uuid(post_id)
    if not post_uuid:
        return jsonify({'error': 'Invalid post id'}), 400

    post = db.session.get(ForumPost, post_uuid)
    if not post or post.is_locked:
        return jsonify({'error': 'Post is locked or not found'}), 403
    
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'Comment content is required'}), 400

    parent_id = data.get('parent_comment_id')
    parent = None
    if parent_id:
        parent_uuid = safe_uuid(parent_id)
        if not parent_uuid:
            return jsonify({'error': 'Invalid parent comment id'}), 400
        parent = db.session.get(Comment, parent_uuid)
        if not parent:
            return jsonify({'error': 'Parent comment not found'}), 404
    
    comment = Comment(
        id=uuid.uuid4(),
        content=content,
        user_id=user.id,
        post_id=post.id,
        parent_comment_id=parent.id if parent else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.session.add(comment)
    post.last_activity_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'id': str(comment.id), 'message': 'Comment added'})


@app.route('/api/forum/posts/<post_id>', methods=['DELETE'])
@login_required
def delete_post(post_id):
    user = get_current_user()
    post_uuid = safe_uuid(post_id)
    if not post_uuid:
        return jsonify({'error': 'Invalid post id'}), 400

    post = db.session.get(ForumPost, post_uuid)
    if not post:
        return jsonify({'error': 'Post not found'}), 404

    can_delete = (
        str(post.user_id) == str(user.id) or
        user.is_moderator or
        user.is_admin
    )
    if not can_delete:
        return jsonify({'error': 'You do not have permission to delete this post'}), 403

    category = db.session.get(ForumCategory, post.category_id) if post.category_id else None
    if category and category.post_count and category.post_count > 0:
        category.post_count -= 1

    db.session.delete(post)
    db.session.commit()
    return jsonify({'message': 'Post deleted'})


@app.route('/api/forum/comments/<comment_id>', methods=['DELETE'])
@login_required
def delete_comment(comment_id):
    user = get_current_user()
    comment_uuid = safe_uuid(comment_id)
    if not comment_uuid:
        return jsonify({'error': 'Invalid comment id'}), 400

    comment = db.session.get(Comment, comment_uuid)
    if not comment:
        return jsonify({'error': 'Comment not found'}), 404

    can_delete = (
        str(comment.user_id) == str(user.id) or
        user.is_moderator or
        user.is_admin
    )
    if not can_delete:
        return jsonify({'error': 'You do not have permission to delete this comment'}), 403

    post = comment.post
    db.session.delete(comment)
    if post:
        post.last_activity_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'message': 'Comment deleted'})

# Vote routes

@app.route('/api/forum/vote', methods=['POST'])
@login_required
def add_vote():
    if not check_rate_limit('vote'):
        return jsonify({'error': 'Rate limit exceeded'}), 429
    
    data = get_request_data()
    user = get_current_user()
    
    post_id = data.get('post_id')
    comment_id = data.get('comment_id')
    value = data.get('value')
    
    if value not in [-1, 0, 1]:
        return jsonify({'error': 'Invalid vote value'}), 400
    
    vote = None
    if post_id:
        vote = Vote.query.filter_by(
            user_id=user.id,
            post_id=post_id
        ).first()
    elif comment_id:
        vote = Vote.query.filter_by(
            user_id=user.id,
            comment_id=comment_id
        ).first()
    else:
        return jsonify({'error': 'Missing post_id or comment_id'}), 400
    
    if value == 0:
        if vote:
            db.session.delete(vote)
    else:
        if vote:
            if vote.vote_value == value:
                db.session.delete(vote)
                value = 0
            else:
                vote.vote_value = value
                vote.updated_at = datetime.utcnow()
        else:
            vote = Vote(
                id=uuid.uuid4(),
                user_id=user.id,
                post_id=post_id,
                comment_id=comment_id,
                vote_value=value,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.session.add(vote)
    
    db.session.commit()
    
    if post_id:
        score = db.session.query(db.func.coalesce(db.func.sum(Vote.vote_value), 0)).filter(Vote.post_id == post_id).scalar()
        user_vote = Vote.query.filter_by(user_id=user.id, post_id=post_id).first()
    else:
        score = db.session.query(db.func.coalesce(db.func.sum(Vote.vote_value), 0)).filter(Vote.comment_id == comment_id).scalar()
        user_vote = Vote.query.filter_by(user_id=user.id, comment_id=comment_id).first()
    
    return jsonify({
        'score': int(score or 0),
        'user_vote': user_vote.vote_value if user_vote else 0
    })

# Wiki routes

@app.route('/api/wiki/articles', methods=['GET'])
def list_articles():
    if not github_manager or not github_manager.authenticated:
        return jsonify({'error': 'GitHub not configured'}), 503
    
    articles = github_manager.get_article_list()
    return jsonify(articles)

@app.route('/api/wiki/articles/<path:slug>', methods=['GET'])
def get_wiki_article(slug):
    if not github_manager or not github_manager.authenticated:
        return jsonify({'error': 'GitHub not configured'}), 503
    
    slug = slug.replace('.md', '')
    article = github_manager.get_article(slug)
    
    if not article:
        return jsonify({'error': 'Article not found'}), 404
    
    html_content = sanitize_markdown(article['content'])
    
    return jsonify({
        'slug': slug,
        'title': slug.replace('-', ' ').replace('_', ' ').title(),
        'content': article['content'],
        'html_content': html_content,
        'sha': article['sha'],
        'last_modified': article.get('last_modified', 'Recently'),
        'url': article.get('html_url')
    })

@app.route('/api/wiki/articles/new', methods=['POST'])
@contributor_or_moderator_required
def create_wiki_article():
    if not check_rate_limit('wiki_edit'):
        return jsonify({'error': 'Rate limit exceeded'}), 429
    
    if not github_manager or not github_manager.authenticated:
        return jsonify({'error': 'GitHub not configured'}), 503
    
    data = get_request_data()
    user = get_current_user()
    
    title = (data.get('title') or '').strip()
    slug = (data.get('slug') or '').strip().lower().replace(' ', '-').replace('_', '-')
    content = (data.get('content') or '').strip()

    if not title or not slug or not content:
        return jsonify({'error': 'Title, slug and content are required'}), 400
    
    existing = github_manager.get_article(slug)
    if existing:
        return jsonify({'error': 'Article already exists'}), 400
    
    branch_name = github_manager.create_edit_branch()
    if not branch_name:
        return jsonify({'error': 'Failed to create branch'}), 500
    
    success = github_manager.submit_edit(
        slug=slug,
        content=content,
        edit_comment=f"New article: {title}",
        branch_name=branch_name,
        author_name=user.username
    )
    
    if not success:
        return jsonify({'error': 'Failed to submit edit'}), 500
    
    pr = github_manager.create_pull_request(
        branch_name, 
        f"New article: {title}", 
        f"Created by: {user.username}\n\nSlug: {slug}"
    )
    
    wiki_edit = WikiEdit(
        id=uuid.uuid4(),
        article_slug=slug,
        article_title=title,
        is_new_article=True,
        content_after=content,
        edit_comment=f"New article: {title}",
        git_branch=branch_name,
        git_pr_url=pr.html_url if pr else None,
        user_id=user.id,
        status='pending'
    )
    
    db.session.add(wiki_edit)
    db.session.commit()
    
    return jsonify({
        'id': str(wiki_edit.id),
        'pr_url': pr.html_url if pr else None,
        'message': 'New article submitted for review'
    })

@app.route('/api/wiki/articles/<path:slug>/edit', methods=['POST'])
@login_required
def submit_wiki_edit(slug):
    if not check_rate_limit('wiki_edit'):
        return jsonify({'error': 'Rate limit exceeded'}), 429
    
    if not github_manager or not github_manager.authenticated:
        return jsonify({'error': 'GitHub not configured'}), 503
    
    data = get_request_data()
    user = get_current_user()
    slug = slug.replace('.md', '')
    content = (data.get('content') or '').rstrip()
    if not content:
        return jsonify({'error': 'Edited content is required'}), 400
    
    current_article = github_manager.get_article(slug)
    if not current_article:
        return jsonify({'error': 'Article not found'}), 404
    
    content_before = current_article['content']
    
    branch_name = github_manager.create_edit_branch()
    if not branch_name:
        return jsonify({'error': 'Failed to create branch'}), 500
    
    success = github_manager.submit_edit(
        slug=slug,
        content=content,
        edit_comment=data.get('comment', 'Wiki edit submission'),
        branch_name=branch_name,
        author_name=user.username
    )
    
    if not success:
        return jsonify({'error': 'Failed to submit edit'}), 500
    
    pr = github_manager.create_pull_request(
        branch_name,
        f"Wiki edit: {slug}",
        f"Submitted by: {user.username}\n\nComment: {data.get('comment', 'No comment provided')}"
    )
    
    wiki_edit = WikiEdit(
        id=uuid.uuid4(),
        article_slug=slug,
        article_title=data.get('title', slug.replace('-', ' ').title()),
        is_new_article=False,
        content_before=content_before,
        content_after=content,
        edit_comment=data.get('comment'),
        git_branch=branch_name,
        git_pr_url=pr.html_url if pr else None,
        user_id=user.id,
        status='pending'
    )
    
    db.session.add(wiki_edit)
    db.session.commit()
    
    return jsonify({
        'id': str(wiki_edit.id),
        'pr_url': pr.html_url if pr else None,
        'message': 'Edit submitted for review'
    })

@app.route('/api/wiki/edits/pending', methods=['GET'])
@login_required
def get_pending_edits():
    user = get_current_user()
    
    pending_edits = WikiEdit.query.filter_by(status='pending').order_by(WikiEdit.created_at.desc()).all()
    
    result = []
    for edit in pending_edits:
        edit_dict = edit.to_dict()
        
        user_vote = WikiEditVote.query.filter_by(
            edit_id=edit.id,
            user_id=user.id
        ).first()
        
        edit_dict['user_voted'] = user_vote is not None
        edit_dict['user_vote_value'] = user_vote.vote_value if user_vote else None
        
        result.append(edit_dict)
    
    return jsonify(result)

@app.route('/api/wiki/edits/<edit_id>/vote', methods=['POST'])
@contributor_or_moderator_required
def vote_on_edit(edit_id):
    if not check_rate_limit('wiki_vote'):
        return jsonify({'error': 'Rate limit exceeded'}), 429
    
    data = get_request_data()
    user = get_current_user()
    
    edit_uuid = safe_uuid(edit_id)
    if not edit_uuid:
        return jsonify({'error': 'Invalid edit id'}), 400
    edit = db.session.get(WikiEdit, edit_uuid)
    if not edit:
        return jsonify({'error': 'Edit not found'}), 404
    
    existing_vote = WikiEditVote.query.filter_by(
        edit_id=edit.id,
        user_id=user.id
    ).first()
    
    if existing_vote:
        return jsonify({'error': 'Already voted'}), 400
    
    vote = WikiEditVote(
        id=uuid.uuid4(),
        edit_id=edit.id,
        user_id=user.id,
        vote_value=data['approve']
    )
    
    db.session.add(vote)
    
    if data['approve']:
        edit.votes_for += 1
    else:
        edit.votes_against += 1
    
    db.session.commit()
    
    if edit.votes_for >= 3 and edit.status == 'pending':
        edit.status = 'approved'
        
        if edit.git_pr_url and github_manager and github_manager.authenticated:
            try:
                pr_number = int(edit.git_pr_url.split('/')[-1])
                if github_manager.merge_pull_request(pr_number):
                    edit.status = 'merged'
                    edit.merged_at = datetime.utcnow()
                    edit.merged_by = user.id
            except:
                pass
        
        db.session.commit()
    
    return jsonify({
        'votes_for': edit.votes_for,
        'votes_against': edit.votes_against,
        'status': edit.status
    })

# Mod rules

@app.route('/api/moderation/report', methods=['POST'])
@login_required
def report_content():
    if not check_rate_limit('report'):
        return jsonify({'error': 'Rate limit exceeded'}), 429
    
    data = get_request_data()
    user = get_current_user()
    
    print(f"Report from {user.username}: {data}")
    
    return jsonify({'message': 'Report submitted'})


@app.route('/api/moderation/users', methods=['GET'])
@moderator_required
def moderation_users():
    users = User.query.order_by(User.created_at.desc()).all()

    results = []
    for user in users:
        results.append({
            'id': str(user.id),
            'username': user.username,
            'email': user.email,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'last_seen_at': user.last_seen_at.isoformat() if user.last_seen_at else None,
            'is_contributor': user.is_contributor,
            'is_moderator': user.is_moderator,
            'is_admin': user.is_admin,
            'post_count': ForumPost.query.filter_by(user_id=user.id).count(),
            'comment_count': Comment.query.filter_by(user_id=user.id).count(),
            'hidden_post_count': ForumPost.query.filter_by(user_id=user.id, is_hidden=True).count(),
            'hidden_comment_count': Comment.query.filter_by(user_id=user.id, is_hidden=True).count(),
        })

    return jsonify(results)


@app.route('/api/moderation/users/<user_id>', methods=['GET'])
@moderator_required
def moderation_user_detail(user_id):
    user_uuid = safe_uuid(user_id)
    if not user_uuid:
        return jsonify({'error': 'Invalid user id'}), 400

    user = db.session.get(User, user_uuid)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    posts = ForumPost.query.filter_by(user_id=user.id).order_by(ForumPost.created_at.desc()).all()
    comments = Comment.query.filter_by(user_id=user.id).order_by(Comment.created_at.desc()).all()
    actions = ModerationAction.query.filter_by(moderator_id=user.id).order_by(ModerationAction.created_at.desc()).limit(20).all()

    return jsonify({
        'user': {
            'id': str(user.id),
            'username': user.username,
            'email': user.email,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'last_seen_at': user.last_seen_at.isoformat() if user.last_seen_at else None,
            'is_contributor': user.is_contributor,
            'is_moderator': user.is_moderator,
            'is_admin': user.is_admin,
        },
        'posts': [
            {
                'id': str(post.id),
                'title': post.title,
                'content': post.content,
                'created_at': post.created_at.isoformat() if post.created_at else None,
                'updated_at': post.updated_at.isoformat() if post.updated_at else None,
                'is_hidden': post.is_hidden,
                'is_locked': post.is_locked,
                'category_name': post.category.name if post.category else None,
                'comment_count': len(post.comments),
                'vote_score': int(db.session.query(db.func.coalesce(db.func.sum(Vote.vote_value), 0)).filter(Vote.post_id == post.id).scalar() or 0),
            }
            for post in posts
        ],
        'comments': [
            {
                'id': str(comment.id),
                'content': comment.content,
                'created_at': comment.created_at.isoformat() if comment.created_at else None,
                'updated_at': comment.updated_at.isoformat() if comment.updated_at else None,
                'is_hidden': comment.is_hidden,
                'is_deleted': comment.is_deleted,
                'post_id': str(comment.post_id) if comment.post_id else None,
                'post_title': comment.post.title if comment.post else None,
                'parent_comment_id': str(comment.parent_comment_id) if comment.parent_comment_id else None,
                'vote_score': int(db.session.query(db.func.coalesce(db.func.sum(Vote.vote_value), 0)).filter(Vote.comment_id == comment.id).scalar() or 0),
            }
            for comment in comments
        ],
        'moderation_actions': [
            {
                'id': str(action.id),
                'action_type': action.action_type,
                'target_type': action.target_type,
                'target_id': action.target_id,
                'reason': action.reason,
                'created_at': action.created_at.isoformat() if action.created_at else None,
            }
            for action in actions
        ]
    })


@app.route('/api/moderation/users/<user_id>/role', methods=['POST'])
@moderator_required
def moderation_update_user_role(user_id):
    target_uuid = safe_uuid(user_id)
    if not target_uuid:
        return jsonify({'error': 'Invalid user id'}), 400

    target_user = db.session.get(User, target_uuid)
    if not target_user:
        return jsonify({'error': 'User not found'}), 404

    actor = get_current_user()
    data = get_request_data()
    role = (data.get('role') or '').strip().lower()

    role_map = {
        'user': (False, False, False),
        'regular': (False, False, False),
        'contributor': (True, False, False),
        'moderator': (False, True, False),
        'admin': (False, False, True),
    }

    if role not in role_map:
        return jsonify({'error': 'Invalid role'}), 400

    if role == 'admin' and not actor.is_admin:
        return jsonify({'error': 'Only admins can assign the admin role'}), 403

    if target_user.is_admin and not actor.is_admin:
        return jsonify({'error': 'Only admins can change another admin'}), 403

    is_contributor, is_moderator, is_admin = role_map[role]
    target_user.is_contributor = is_contributor
    target_user.is_moderator = is_moderator
    target_user.is_admin = is_admin

    if target_user.is_admin:
        target_user.is_moderator = True
        target_user.is_contributor = True
    elif target_user.is_moderator:
        target_user.is_contributor = True

    action = ModerationAction(
        id=uuid.uuid4(),
        moderator_id=actor.id,
        action_type='set_role',
        target_type='user',
        target_id=target_user.id,
        reason=f"Assigned role: {role}"
    )
    db.session.add(action)
    db.session.commit()

    return jsonify({
        'message': 'Role updated successfully',
        'user': target_user.to_dict()
    })

@app.route('/api/moderation/actions', methods=['POST'])
@moderator_required
def moderation_action():
    data = get_request_data()
    moderator = get_current_user()
    
    action = ModerationAction(
        id=uuid.uuid4(),
        moderator_id=moderator.id,
        action_type=data['action'],
        target_type=data['target_type'],
        target_id=data['target_id'],
        reason=data.get('reason')
    )
    
    db.session.add(action)
    
    if data['target_type'] == 'post':
        target_uuid = safe_uuid(data.get('target_id'))
        if not target_uuid:
            return jsonify({'error': 'Invalid target id'}), 400
        post = db.session.get(ForumPost, target_uuid)
        if post:
            if data['action'] == 'hide':
                post.is_hidden = True
            elif data['action'] == 'unhide':
                post.is_hidden = False
            elif data['action'] == 'lock':
                post.is_locked = True
            elif data['action'] == 'unlock':
                post.is_locked = False
    elif data['target_type'] == 'comment':
        target_uuid = safe_uuid(data.get('target_id'))
        if not target_uuid:
            return jsonify({'error': 'Invalid target id'}), 400
        comment = db.session.get(Comment, target_uuid)
        if comment:
            if data['action'] == 'hide':
                comment.is_hidden = True
            elif data['action'] == 'unhide':
                comment.is_hidden = False
    
    db.session.commit()
    
    return jsonify({'message': f"Content {data['action']}ed"})

# Health

@app.route('/api/health', methods=['GET'])
def health_check():
    user_count = User.query.count() if User else 0
    post_count = ForumPost.query.count() if ForumPost else 0
    
    return jsonify({
        'status': 'healthy',
        'github': 'connected' if github_manager and github_manager.authenticated else 'disconnected',
        'database': 'connected',
        'stats': {
            'users': user_count,
            'forum_posts': post_count,
            'categories': ForumCategory.query.count() if ForumCategory else 0
        },
        'timestamp': datetime.utcnow().isoformat()
    })

# After request

@app.after_request
def after_request(response):
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization,X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response

# DB Init

with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created/verified")
        
        if ForumCategory.query.count() == 0:
            default_categories = [
                ForumCategory(id=1, name="General Discussion", slug="general", 
                            description="General privacy discussions", color="#2b7a4b"),
                ForumCategory(id=2, name="Beginners Corner", slug="beginners", 
                            description="Ask questions and learn basics", color="#17a2b8"),
                ForumCategory(id=3, name="Tools & Software", slug="tools", 
                            description="Privacy tools and recommendations", color="#fd7e14"),
                ForumCategory(id=4, name="News & Updates", slug="news", 
                            description="Privacy news and policy changes", color="#dc3545"),
                ForumCategory(id=5, name="Threat Modeling", slug="threats", 
                            description="Discuss specific threats", color="#6f42c1"),
            ]
            for cat in default_categories:
                db.session.add(cat)
            db.session.commit()
            print(f"Created {len(default_categories)} categories")
        

            
    except Exception as e:
        print(f"Database initialization warning: {e}")

# Server start

if __name__ == '__main__':
    print("\n" + "="*60)
    print("API Server")
    print("="*60)
    
    if github_manager and github_manager.authenticated:
        print(f"\nGitHub Repository: {GITHUB_OWNER}/{GITHUB_REPO}")
    
    with app.app_context():
        user_count = User.query.count()
        print(f"\n Users in database: {user_count}")
        print(f"Forum posts: {ForumPost.query.count()}")
        print(f"Categories: {ForumCategory.query.count()}")
    
    print("\n Available endpoints:")
    print("   POST   /api/auth/register - Register")
    print("   POST   /api/auth/login - Login")
    print("   POST   /api/auth/logout - Logout")
    print("   GET    /api/auth/me - Current user")
    print("   GET    /api/health - Health check")
    print("   GET    /api/forum/categories - Forum categories")
    print("   GET    /api/forum/posts - Forum posts")
    print("   POST   /api/forum/posts - Create post")
    print("   GET    /api/wiki/articles - Wiki articles")
    print("   GET    /api/wiki/articles/<slug> - Get article")
    
    print(f"\nServer running on: http://127.0.0.1:5000")
    print("="*60 + "\n")
    
    app.run(debug=True, host='127.0.0.1', port=5000)