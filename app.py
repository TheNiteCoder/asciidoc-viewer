#!/usr/bin/env python3

import tornado.ioloop
import tornado.web
import os.path
import sys
import tempfile
import subprocess

from bs4 import BeautifulSoup

from argparse import ArgumentParser

class Messenger:
    def __init__(self):
        self.items = []
    def queue(self, msg):
        self.items.append(msg)

messenger = Messenger()

def is_hidden(filename):
    name = os.path.basename(os.path.abspath(filename))
    return name.startswith('.')

def check_valid_filename(filename):
    return not is_hidden(filename)

class ProcessHandler:
    def __init__(self, args):
        process = subprocess.run(args, text=True)
        self.stdout = process.stdout
        self.stderr = process.stderr
        self.returncode = process.returncode

def get_html_element(name, source):
    soup = BeautifulSoup(source, 'html.parser')
    body = soup.find(name)
    if body is None:
        return None
    content = ''
    for item in body.contents:
        content += str(item)
    return content

class PageRenderer:
    def __init__(self, filename):
        self.filename = filename
    def render(self, dark=False):
        tmpdir = tempfile.TemporaryDirectory(dir='/tmp/')
        args = ['asciidoc']
        if dark:
            args = args + ['--theme', 'asciidoc-dark.css']
        else:
            args = args + ['--theme', 'asciidoc-light.css']
        args = args + ['-o', os.path.join(tmpdir.name, 'out.html'), self.filename]
        handler = ProcessHandler(args)
        if handler.returncode != 0:
            print(handler.stdout)
        html = None
        with open(os.path.join(tmpdir.name, 'out.html')) as f:
            self.html = f.read()
        tmpdir.cleanup()

def remove_root_path(root, path):
    result = ''
    root_items = root.split(os.sep)
    path_items = path.split(os.sep)

    pathIsAbs = False
    if root_items[0] == '':
        root_items = root_items[1:]
    if path_items[0] == '':
        pathIsAbs = True
        path_items = path_items[1:]
    trigger = False
    while path_items[0] == root_items[0]:
        if len(root_items) == 1:
            trigger = True
        path_items = path_items[1:]
        root_items = root_items[1:]
        if trigger:
            break
    return os.path.join(*path_items)   
    
class InternalError(BaseException):
    def __init__(self, msg):
        self.msg = msg
    def __str__(self):
        return self.msg

class FileFinder:
    def __init__(self, dir):
        self.dir = dir
        if not os.path.isdir(self.dir):
            raise InternalError('{} is not a directory'.format(self.dir))
    def search(self, searchterm, case=True):
        l = []
        if not os.path.isdir(self.dir):
            raise InternalError('{} is not a directory'.format(self.dir))
        for root, dirs, files in os.walk(self.dir):
            for name in files:
                if not check_valid_filename(name):
                    continue
                if case:
                    if not name.find(searchterm) == -1:
                        l.append(os.path.join(root, name))
                else:
                    if not name.lower().find(searchterm.lower()) == -1:
                        l.append(os.path.join(root, name))
        return l

class FileContentFinder:
    def __init__(self, dir):
        self.dir = dir
        if not os.path.isdir(self.dir):
            raise InternalError('{} is not a directory'.format(self.dir))
    def search(self, term, case=True):
        l = []
        if not case:
            term = term.lower()
        if not os.path.isdir(self.dir):
            raise InternalError('{} is not a directory'.format(self.dir))
        for root, dirs, files in os.walk(self.dir):
            for name in files:
                if not check_valid_filename(name):
                    continue
                with open(os.path.join(root, name), 'r') as f:
                    content = f.read()
                    if not case:
                        content = content.lower()
                    if content.find(term) != -1:
                        l.append(os.path.join(root, name))
        return l

class BaseHandler(tornado.web.RequestHandler):
    def initialize(self, options):
        self.options = options
    def special_render(self, __name, **options):
        self.render(__name, name=self.options['name'], messages=messenger.items, dark_mode=(self.get_cookie('dark_mode') == 'true'), **options)

class RootHandler(BaseHandler):
    def get(self):
        self.special_render('base.template.html')

class OptionsHandler(BaseHandler):
    def post(self):
        dark_mode = self.get_argument('dark_mode', 'false')
        dark_mode = dark_mode == 'true'
        if dark_mode:
            self.set_cookie('dark_mode', 'true')
        else:
            self.set_cookie('dark_mode', 'false')
        self.redirect('/')

class SearchHandler(BaseHandler):
    def get_search_results(self, term):
        files1 = FileFinder(self.options['source']).search(term, case=False)
        files2 = FileContentFinder(self.options['source']).search(term, case=False)
        files1 = [remove_root_path(self.options['source'], f) for f in files1]
        files2 = [remove_root_path(self.options['source'], f) for f in files2]
        nodups = list(set(files1 + files2))
        noinvalid = filter(lambda x: check_valid_filename(x), nodups)
        return noinvalid
    def get(self):
        self.redirect('/')
    def post(self):
        self.special_render('search.template.html', results=self.get_search_results(self.get_argument('search')))

class PageHandler(BaseHandler):
    def get(self):
        name = self.get_argument('name')
        if name is None or not check_valid_filename(name):
            self.redirect('/')
        name = os.path.join(self.options['source'], name)
        renderer = PageRenderer(name)
        renderer.render(dark=(self.get_cookie('dark_mode', 'false') == 'true'))
        body_text = get_html_element('body', renderer.html)
        head_text = get_html_element('head', renderer.html)
        self.special_render('page.template.html', page_head=head_text, page_body=body_text)

class TreeHandler(BaseHandler):
    def get(self):
        l = []
        for root, dirs, files in os.walk(self.options['source']):
            for name in files:
                l.append(remove_root_path(self.options['source'], os.path.join(root, name)))
        l = filter(lambda x: check_valid_filename(x), l)
        self.special_render('tree.template.html', tree=l)

def create_options(source="/doc", name="AsciiDoc Viewer", home_page='home.adoc'):
    return dict(source=source, name=name, home_page=home_page)

def create_app(options):
    app = tornado.web.Application([
        (r"/", RootHandler, dict(options=options)),
        (r"/page", PageHandler, dict(options=options)),
        (r"/search", SearchHandler, dict(options=options)),
        (r"/tree", TreeHandler, dict(options=options)),
        (r"/options", OptionsHandler, dict(options=options)),
    ], template_path='templates', static_path='static')
    return app

def main():
    parser = ArgumentParser()
    parser.add_argument('--port', type=int, help="Port to listen on", default=2959)
    parser.add_argument('--source', type=str, help="Directory containing source asciidoc", default=".")
    parser.add_argument('--name', type=str, help="Name", default="AsciiDoc Viewer")
    args = parser.parse_args()
    options = create_options(source=args.source, name=args.name)
    app = create_app(options)
    app.listen(args.port)
    try:
        tornado.ioloop.IOLoop.current().start()
    except InternalError as e:
        print(e)
        sys.exit(1)

if __name__ == '__main__':
    main()
