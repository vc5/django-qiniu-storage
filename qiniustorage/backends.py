"""
Qiniu Storage Backends
"""
from __future__ import absolute_import
import datetime
import os
from urlparse import urljoin

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

from qiniu import Auth, BucketManager, put_data
import requests

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import Storage
from django.core.exceptions import ImproperlyConfigured

from .utils import bucket_lister


def get_qiniu_config(name):
    """
    Get configuration variable from environment variable
    or django setting.py
    """
    config = os.environ.get(name, getattr(settings, name, None))
    if config:
        return config
    else:
        raise ImproperlyConfigured(
            "Can't find config for '%s' either in environment"
            "variable or in setting.py" % name)


QINIU_ACCESS_KEY = get_qiniu_config('QINIU_ACCESS_KEY')
QINIU_SECRET_KEY = get_qiniu_config('QINIU_SECRET_KEY')
QINIU_BUCKET_NAME = get_qiniu_config('QINIU_BUCKET_NAME')
QINIU_BUCKET_DOMAIN = get_qiniu_config('QINIU_BUCKET_DOMAIN')


class QiniuStorage(Storage):
    """
    Qiniu Storage Service
    """
    location = ""
    def __init__(
            self,
            access_key=QINIU_ACCESS_KEY,
            secret_key=QINIU_SECRET_KEY,
            bucket_name=QINIU_BUCKET_NAME,
            bucket_domain=QINIU_BUCKET_DOMAIN):

        self.auth = Auth(access_key, secret_key)
        self.bucket_name = bucket_name
        self.bucket_domain = bucket_domain
        self.bucket_manager = BucketManager(self.auth)

    def _clean_name(self, name):
        if type(name) is unicode:
            return name.encode('utf-8')
        else:
            return name
    def _normalize_name(self, name):
        return ("%s/%s"% (self.location, name.lstrip('/'))).lstrip('/')

    def _open(self, name, mode='rb'):
        return QiniuFile(name, self, mode)

    def _save(self, name, content):
        name = self._normalize_name(self._clean_name(name))

        if hasattr(content, 'open'):
            # Since Django 1.6, content should be a instance
            # of `django.core.files.File`
            content.open()

        if hasattr(content, 'chunks'):
            content_str = ''.join(chunk for chunk in content.chunks())
        else:
            content_str = content.read()

        self._put_file(name, content_str)
        content.close()
        return name

    def _put_file(self, name, content):
        token = self.auth.upload_token(self.bucket_name)
        ret, info = put_data(token, name, content)
        if ret['key']!= name:
            raise IOError(
                "Failed to put file '%s'. "
                "Error message: %s" % (name, err))

    def _read(self, name):
        return requests.get(self.url(name)).content

    def delete(self, name):
        name = self._normalize_name(self._clean_name(name))
        ret, info = self.bucket_manager.delete(self.bucket_name, name)

        if ret is None or info.status_code ==612:
            raise IOError("Failed to delete file: %s" % name)

    def _file_stat(self, name, silent=False):
        name = self._normalize_name(self._clean_name(name))
        ret, info = self.bucket_manager.stat(self.bucket_name, name)
        if ret is None and not silent:
            raise IOError("Failed to stat file: %s" % name)
        return ret

    def exists(self, name):
        stats = self._file_stat(name, silent=True)
        return True if stats else False

    def size(self, name):
        stats = self._file_stat(name)
        return stats['fsize']

    def modified_time(self, name):
        stats = self._file_stat(name)
        time_stamp = float(stats['putTime'])/10000000
        return datetime.datetime.fromtimestamp(time_stamp)

    def listdir(self, name):
        name = self._normalize_name(self._clean_name(name))
        if name and not name.endswith('/'):
            name += '/'

        dirlist = bucket_lister(self.bucket_manager, self.bucket_name, prefix=name)
        files = []
        dirs = set()
        base_parts = name.split("/")[:-1]
        for item in dirlist:
            parts = item['key'].split("/")
            parts = parts[len(base_parts):]
            if len(parts) == 1:
                # File
                files.append(parts[0])
            elif len(parts) > 1:
                # Directory
                dirs.add(parts[0])
        return list(dirs), files

    def url(self, name):
        name = self._normalize_name(self._clean_name(name))
        return urljoin("http://" + self.bucket_domain, name)

class QiniuMediaStorage(QiniuStorage):
    location = settings.MEDIA_ROOT.strip('/')

class QiniuStaticStorage(QiniuStorage):
    location = settings.STATIC_ROOT.strip('/')

class QiniuFile(File):
    def __init__(self, name, storage, mode):
        self._storage = storage
        self._name = name[len(self._storage.location):].lstrip('/')
        self._mode = mode
        self.file = StringIO()
        self._is_dirty = False
        self._is_read = False

    @property
    def size(self):
        if not hasattr(self, '_size'):
            self._size = self._storage.size(self._name)
        return self._size

    def read(self, num_bytes=None):
        if not self._is_read:
            self.file = StringIO(self._storage._read(self._name))
            self._is_read = True

        if num_bytes is None:
            return self.file.read()
        else:
            return self.file.read(num_bytes) 

    def write(self, content):
        if 'w' not in self._mode:
            raise AttributeError("File was opened for read-only access.")
        self.file.write(content)
        self._is_dirty = True
        self._is_read = True

    def close(self):
        if self._is_dirty:
            self._storage._put_file(self._name, self.file.getvalue())
        self.file.close()
