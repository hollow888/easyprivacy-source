# models.py
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime)
    
    # User roles
    is_contributor = db.Column(db.Boolean, default=False)
    is_moderator = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    
    # Relationships
    forum_posts = db.relationship('ForumPost', backref='author', lazy=True)
    comments = db.relationship('Comment', backref='author', lazy=True)
    votes = db.relationship('Vote', backref='user', lazy=True)
    
    # Wiki edits where user is the submitter
    wiki_edits = db.relationship(
        'WikiEdit', 
        foreign_keys='WikiEdit.user_id',
        backref='submitter', 
        lazy=True
    )
    
    # Wiki edits that user merged (as moderator)
    merged_edits = db.relationship(
        'WikiEdit',
        foreign_keys='WikiEdit.merged_by',
        backref='merger',
        lazy=True
    )
    
    wiki_edit_votes = db.relationship('WikiEditVote', backref='user', lazy=True)
    moderation_actions = db.relationship('ModerationAction', backref='moderator', lazy=True)
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'username': self.username,
            'email': self.email,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_contributor': self.is_contributor,
            'is_moderator': self.is_moderator,
            'is_admin': self.is_admin
        }

class ForumCategory(db.Model):
    __tablename__ = 'forum_categories'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    color = db.Column(db.String(7), default='#2b7a4b')
    post_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    posts = db.relationship('ForumPost', backref='category', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'color': self.color,
            'post_count': self.post_count
        }

class ForumPost(db.Model):
    __tablename__ = 'forum_posts'
    
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('forum_categories.id'), nullable=False)
    
    is_pinned = db.Column(db.Boolean, default=False)
    is_locked = db.Column(db.Boolean, default=False)
    is_hidden = db.Column(db.Boolean, default=False)
    view_count = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_activity_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    comments = db.relationship('Comment', backref='post', lazy=True, cascade='all, delete-orphan')
    votes = db.relationship('Vote', backref='post', lazy=True, cascade='all, delete-orphan')
    tags = db.relationship('Tag', secondary='post_tags', lazy=True)
    
    def to_dict(self):
        vote_score = db.session.query(db.func.coalesce(db.func.sum(Vote.vote_value), 0)).filter(Vote.post_id == self.id).scalar() if self.votes else 0
        
        return {
            'id': str(self.id),
            'title': self.title,
            'content': self.content,
            'author': self.author.username if self.author else 'Unknown',
            'author_id': str(self.user_id) if self.user_id else None,
            'author_is_mod': self.author.is_moderator if self.author else False,
            'author_is_contributor': self.author.is_contributor if self.author else False,
            'category_id': self.category_id,
            'category_name': self.category.name if self.category else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'vote_score': int(vote_score or 0),
            'view_count': self.view_count,
            'is_pinned': self.is_pinned,
            'is_locked': self.is_locked,
            'is_hidden': self.is_hidden,
            'tags': [{'name': t.name, 'slug': t.slug} for t in self.tags],
            'comment_count': len(self.comments)
        }

class Comment(db.Model):
    __tablename__ = 'comments'
    
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content = db.Column(db.Text, nullable=False)
    
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    post_id = db.Column(UUID(as_uuid=True), db.ForeignKey('forum_posts.id'), nullable=False)
    parent_comment_id = db.Column(UUID(as_uuid=True), db.ForeignKey('comments.id'))
    
    is_hidden = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    votes = db.relationship('Vote', backref='comment', lazy=True, cascade='all, delete-orphan')
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy=True)
    
    def to_dict(self):
        vote_score = db.session.query(db.func.coalesce(db.func.sum(Vote.vote_value), 0)).filter(Vote.comment_id == self.id).scalar() if self.votes else 0
        
        # Get replies (not hidden, not deleted)
        reply_list = []
        for reply in self.replies:
            if not reply.is_hidden and not reply.is_deleted:
                reply_list.append(reply.to_dict())
        
        return {
            'id': str(self.id),
            'content': self.content,
            'author': self.author.username if self.author else 'Unknown',
            'author_id': str(self.user_id) if self.user_id else None,
            'author_is_mod': self.author.is_moderator if self.author else False,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'vote_score': int(vote_score or 0),
            'is_hidden': self.is_hidden,
            'parent_id': str(self.parent_comment_id) if self.parent_comment_id else None,
            'replies': reply_list
        }

class Tag(db.Model):
    __tablename__ = 'tags'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    color = db.Column(db.String(7), default='#2b7a4b')
    usage_count = db.Column(db.Integer, default=0)

class PostTags(db.Model):
    __tablename__ = 'post_tags'
    
    post_id = db.Column(UUID(as_uuid=True), db.ForeignKey('forum_posts.id'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('tags.id'), primary_key=True)

class Vote(db.Model):
    __tablename__ = 'votes'
    
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    post_id = db.Column(UUID(as_uuid=True), db.ForeignKey('forum_posts.id'))
    comment_id = db.Column(UUID(as_uuid=True), db.ForeignKey('comments.id'))
    vote_value = db.Column(db.SmallInteger)  # -1 or 1
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'post_id', name='unique_user_post_vote'),
        db.UniqueConstraint('user_id', 'comment_id', name='unique_user_comment_vote'),
        CheckConstraint('vote_value IN (-1, 1)', name='check_vote_value'),
        CheckConstraint('(post_id IS NOT NULL AND comment_id IS NULL) OR (post_id IS NULL AND comment_id IS NOT NULL)', name='check_vote_target'),
    )

class WikiEdit(db.Model):
    __tablename__ = 'wiki_edits'
    
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Article info
    article_slug = db.Column(db.String(255), nullable=False)
    article_title = db.Column(db.String(255), nullable=False)
    is_new_article = db.Column(db.Boolean, default=False)
    
    # Content
    content_before = db.Column(db.Text)
    content_after = db.Column(db.Text, nullable=False)
    edit_comment = db.Column(db.Text)
    
    # Git info
    git_branch = db.Column(db.String(255))
    git_commit_hash = db.Column(db.String(64))
    git_pr_url = db.Column(db.String(255))
    
    # Status
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected, merged
    votes_for = db.Column(db.Integer, default=0)
    votes_against = db.Column(db.Integer, default=0)
    
    # User relationship - submitter
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    merged_at = db.Column(db.DateTime)
    
    # Who merged it (moderator)
    merged_by = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'))
    
    # Relationships
    votes = db.relationship('WikiEditVote', backref='edit', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'article_slug': self.article_slug,
            'article_title': self.article_title,
            'is_new_article': self.is_new_article,
            'author': self.submitter.username if self.submitter else 'Unknown',
            'author_id': str(self.user_id) if self.user_id else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'edit_comment': self.edit_comment,
            'votes_for': self.votes_for,
            'votes_against': self.votes_against,
            'status': self.status,
            'pr_url': self.git_pr_url
        }

class WikiEditVote(db.Model):
    __tablename__ = 'wiki_edit_votes'
    
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    edit_id = db.Column(UUID(as_uuid=True), db.ForeignKey('wiki_edits.id'), nullable=False)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    vote_value = db.Column(db.Boolean)  # true = approve, false = reject
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('edit_id', 'user_id', name='unique_edit_user_vote'),)

class ModerationAction(db.Model):
    __tablename__ = 'moderation_actions'
    
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    moderator_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    
    action_type = db.Column(db.String(50))  # hide, remove, lock, warn, promote, demote
    target_type = db.Column(db.String(20))  # post, comment, user
    target_id = db.Column(UUID(as_uuid=True))
    reason = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'moderator': self.moderator.username if self.moderator else 'Unknown',
            'action_type': self.action_type,
            'target_type': self.target_type,
            'target_id': str(self.target_id) if self.target_id else None,
            'reason': self.reason,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class RateLimit(db.Model):
    __tablename__ = 'rate_limits'
    
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    action_type = db.Column(db.String(50))
    action_count = db.Column(db.Integer, default=1)
    first_action_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_action_at = db.Column(db.DateTime, default=datetime.utcnow)