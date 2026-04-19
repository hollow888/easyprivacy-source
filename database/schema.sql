-- database/schema.sql
-- Forum and Wiki Database Schema for EasyPrivacy

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table (minimal data for privacy)
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(50) UNIQUE,
    is_anonymous BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    -- No email, no personal data stored
    preferences JSONB DEFAULT '{}'::jsonb
);

-- Forum categories
CREATE TABLE forum_categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    color VARCHAR(7) DEFAULT '#2b7a4b',
    icon VARCHAR(50),
    post_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Forum posts
CREATE TABLE forum_posts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    anonymous_name VARCHAR(50), -- For anonymous posts
    category_id INTEGER REFERENCES forum_categories(id),
    is_pinned BOOLEAN DEFAULT FALSE,
    is_locked BOOLEAN DEFAULT FALSE,
    is_hidden BOOLEAN DEFAULT FALSE,
    view_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Post tags
CREATE TABLE tags (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    slug VARCHAR(50) UNIQUE NOT NULL,
    color VARCHAR(7) DEFAULT '#2b7a4b',
    usage_count INTEGER DEFAULT 0
);

-- Post-tag junction
CREATE TABLE post_tags (
    post_id UUID REFERENCES forum_posts(id) ON DELETE CASCADE,
    tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, tag_id)
);

-- Comments (threaded)
CREATE TABLE comments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    content TEXT NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    anonymous_name VARCHAR(50),
    post_id UUID REFERENCES forum_posts(id) ON DELETE CASCADE,
    parent_comment_id UUID REFERENCES comments(id) ON DELETE CASCADE,
    is_hidden BOOLEAN DEFAULT FALSE,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    depth INTEGER DEFAULT 0,
    path TEXT -- For efficient threading queries
);

-- Votes (for posts and comments)
CREATE TABLE votes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    anonymous_session_id VARCHAR(255), -- For anonymous voting (session-based)
    post_id UUID REFERENCES forum_posts(id) ON DELETE CASCADE,
    comment_id UUID REFERENCES comments(id) ON DELETE CASCADE,
    vote_value SMALLINT CHECK (vote_value IN (-1, 1)),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    -- Ensure one vote per user/session per item
    CONSTRAINT one_vote_per_item CHECK (
        (post_id IS NOT NULL AND comment_id IS NULL) OR
        (post_id IS NULL AND comment_id IS NOT NULL)
    ),
    UNIQUE(user_id, post_id) DEFERRABLE,
    UNIQUE(user_id, comment_id) DEFERRABLE,
    UNIQUE(anonymous_session_id, post_id) DEFERRABLE,
    UNIQUE(anonymous_session_id, comment_id) DEFERRABLE
);

-- Wiki edit submissions (Git integration)
CREATE TABLE wiki_edits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    article_slug VARCHAR(255) NOT NULL,
    article_title VARCHAR(255) NOT NULL,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    anonymous_name VARCHAR(50),
    git_branch VARCHAR(255) NOT NULL,
    git_commit_hash VARCHAR(64),
    git_pr_url VARCHAR(255),
    content_before TEXT,
    content_after TEXT NOT NULL,
    edit_comment TEXT,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'merged')),
    votes_for INTEGER DEFAULT 0,
    votes_against INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    merged_at TIMESTAMP WITH TIME ZONE,
    merged_by UUID REFERENCES users(id)
);

-- Wiki edit votes
CREATE TABLE wiki_edit_votes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    edit_id UUID REFERENCES wiki_edits(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    anonymous_session_id VARCHAR(255),
    vote_value BOOLEAN NOT NULL, -- true = approve, false = reject
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(edit_id, user_id),
    UNIQUE(edit_id, anonymous_session_id)
);

-- Moderation actions
CREATE TABLE moderation_actions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    moderator_id UUID REFERENCES users(id),
    action_type VARCHAR(50) NOT NULL, -- 'hide', 'remove', 'lock', 'warn'
    target_type VARCHAR(20) NOT NULL, -- 'post', 'comment', 'user'
    target_id UUID NOT NULL,
    reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Rate limiting
CREATE TABLE rate_limits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id VARCHAR(255),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    action_type VARCHAR(50) NOT NULL,
    action_count INTEGER DEFAULT 1,
    first_action_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_action_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, action_type),
    UNIQUE(user_id, action_type)
);

-- Indexes for performance
CREATE INDEX idx_forum_posts_category ON forum_posts(category_id);
CREATE INDEX idx_forum_posts_created ON forum_posts(created_at DESC);
CREATE INDEX idx_forum_posts_last_activity ON forum_posts(last_activity_at DESC);
CREATE INDEX idx_comments_post ON comments(post_id);
CREATE INDEX idx_comments_parent ON comments(parent_comment_id);
CREATE INDEX idx_votes_post ON votes(post_id);
CREATE INDEX idx_votes_comment ON votes(comment_id);
CREATE INDEX idx_wiki_edits_status ON wiki_edits(status);
CREATE INDEX idx_wiki_edits_article ON wiki_edits(article_slug);
CREATE INDEX idx_rate_limits_session ON rate_limits(session_id);

-- Create default categories
INSERT INTO forum_categories (name, slug, description, color) VALUES
    ('General Discussion', 'general', 'General privacy discussions', '#2b7a4b'),
    ('Beginners Corner', 'beginners', 'Ask questions and learn basics', '#17a2b8'),
    ('Tools & Software', 'tools', 'Privacy tools, apps, and software recommendations', '#fd7e14'),
    ('News & Updates', 'news', 'Privacy news, policy changes, and updates', '#dc3545'),
    ('Threat Modeling', 'threats', 'Discuss specific threats and protection strategies', '#6f42c1'),
    ('Hardware & Devices', 'hardware', 'Privacy-focused hardware discussions', '#20c997');

-- Create default tags
INSERT INTO tags (name, slug, color) VALUES
    ('question', 'question', '#17a2b8'),
    ('guide', 'guide', '#28a745'),
    ('warning', 'warning', '#dc3545'),
    ('vpn', 'vpn', '#6f42c1'),
    ('browser', 'browser', '#fd7e14'),
    ('2fa', '2fa', '#20c997'),
    ('passwords', 'passwords', '#007bff'),
    ('email', 'email', '#6610f2'),
    ('social-media', 'social-media', '#e83e8c'),
    ('encryption', 'encryption', '#28a745');