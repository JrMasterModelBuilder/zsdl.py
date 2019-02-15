#!/usr/bin/env python

import os
import sys
import errno
import time
import math
import email.utils as emailutils
import re
import json
import argparse

try:
	from urllib.request import urlopen, Request, HTTPError
except ImportError:
	from urllib2 import urlopen, Request, HTTPError

try:
	from html.parser import HTMLParser
except ImportError:
	from HTMLParser import HTMLParser

try:
	from urllib.parse import quote as urlquote, unquote as urlunquote, urlparse
except ImportError:
	from urllib2 import quote as urlquote, unquote as urlunquote, urlparse
	urlparse = urlparse.urlparse

try:
	from urllib.parse import urljoin
except ImportError:
	from urlparse import urljoin

__prog__ = 'zsdl.py'
__version__ = '1.0.0'
__copyright__ = 'Copyright (c) 2019 JrMasterModelBuilder'
__license__ = 'MPL-2.0'

class Main(object):
	DL_PROGRESS_START = 1
	DL_PROGRESS_READ = 2
	DL_PROGRESS_WROTE = 3
	DL_PROGRESS_DONE = 4

	def __init__(self, options):
		self.options = options
		self._output_progress_max = 0

	def log(self, message, verbose=False, err=False, nl=True):
		if verbose and not self.options.verbose:
			return
		self.output(message, err, nl)

	def output(self, message, err=False, nl=True):
		out = sys.stderr if err else sys.stdout
		out.write(message)
		if nl:
			out.write(os.linesep)
		out.flush()

	def output_progress_start(self):
		self.output_progress_max = 0

	def output_progress(self, message, err=False, nl=True):
		l = max(self._output_progress_max, len(message))
		self._output_progress_max = l
		message_pad = message.ljust(l)
		self.output('\r%s\r' % message_pad, err, False)

	def stat(self, path):
		try:
			return os.stat(path)
		except OSError as ex:
			if ex.errno != errno.ENOENT:
				raise ex
		return None

	def dict_has_props(self, dic, props):
		for p in props:
			if not p in dic:
				return False
		return True

	def assert_status_code(self, code, expected):
		if code != expected:
			raise Exception('Invalid status code: %s expected: %s' % (code, expected))

	def seconds_human(self, seconds):
		m, s = divmod(seconds, 60)
		h, m = divmod(m, 60)
		return '%d:%02d:%02d' % (h, m, s)

	def bytes_human(self, size):
		based = float(size)
		base = 1024
		names = ['B', 'K', 'M', 'G', 'T']
		il = len(names) - 1
		i = 0
		while based > base and i < il:
			based /= base
			i += 1
		return '%.2f%s' % (based, names[i])

	def percent_human(self, part, total):
		f = (part / float(total)) if total else 0
		return '%.2f%%' % (f * 100)

	def json_decode(self, s):
		return json.loads(s)

	def js_decode(self, s):
		# Hex escape sequence support.
		def repl(m):
			p = m.group(0).split('\\x')
			p[1] = json.dumps(chr(int(p[1], 16)))[1:-1]
			return ''.join(p)
		json_clean = re.sub(r'(^|[^\\])(\\\\)*\\x[0-9A-Fa-f]{2}', repl, s)
		return self.json_decode(json_clean)

	def request(self, url, headers):
		r = None
		try:
			req = Request(url, None, headers)
			r = urlopen(req, timeout=self.options.timeout)
		except HTTPError as ex:
			r = ex
		return r

	def request_data(self, url):
		res = self.request(url, {
			'User-Agent': ''
		})
		code = res.getcode()
		headers = res.info()
		body = res.read()
		return (code, headers, body)

	def request_data_decode(self, body, headers):
		# Should use headers to determine the correct encoding.
		return body.decode('utf-8')

	def request_header_get(self, headers, header, cast=None):
		r = headers[header] if header in headers else None
		if cast:
			try:
				r = cast(r)
			except:
				r = None
		return r

	def parse_http_header_date(self, value):
		parsed = emailutils.parsedate_tz(value)
		epoch = emailutils.mktime_tz(parsed)
		return epoch

	def request_download(self, url, dest, progress, cont=False):
		buffer_size = self.options.buffer
		total = None
		modified = None

		# Open the output file, append mode if continue.
		with open(dest, 'ab' if cont else 'wb') as fp:
			status = 200
			headers = {
				'User-Agent': ''
			}

			# Get the file size if continue.
			offset = fp.tell() if cont else 0
			continued = cont and offset
			if continued:
				# Switch to range request if continue from existing.
				status = 206
				headers['Range'] = 'bytes=%s-' % (offset)

			start = time.time()
			progress(self.DL_PROGRESS_START, start, start, offset, 0, offset, None)

			res = self.request(url, headers)
			code = res.getcode()

			# If continued and range is invalid, then no more bytes.
			if continued and code == 416:
				progress(self.DL_PROGRESS_DONE, start, time.time(), offset, 0, offset, offset)
				return

			self.assert_status_code(res.getcode(), status)

			headers = res.info()
			content_length = self.request_header_get(headers, 'content-length', int)
			modified = self.request_header_get(headers, 'last-modified')
			modified = self.parse_http_header_date(modified) if modified else None

			total = None if content_length is None else (offset + content_length)
			size = offset

			while True:
				data = res.read(buffer_size)
				added = len(data)
				if not added:
					break
				size += added
				progress(self.DL_PROGRESS_READ, start, time.time(), offset, added, size, total)
				fp.write(data)
				progress(self.DL_PROGRESS_WROTE, start, time.time(), offset, added, size, total)

			progress(self.DL_PROGRESS_DONE, start, time.time(), offset, 0, size, total)

		return {
			'size': total,
			'modified': modified
		}

	def parse_storage(self, html):
		#<script type="text/javascript">
		#    var a = 42;
		#    document.getElementById('dlbutton').omg = "asdasd".substr(0, 3);
		#    var b = document.getElementById('dlbutton').omg.length;
		#    document.getElementById('dlbutton').href = "/d/uN1qu3/"+(Math.pow(a, 3)+b)+"/file.ext";
		#    if (document.getElementById('fimage')) {
		#        document.getElementById('fimage').href = "/i/uN1qu3/"+(Math.pow(a, 3)+b)+"/file.ext";
		#    }
		#</script>
		class TheHTMLParser(HTMLParser):
			def __init__(self):
				HTMLParser.__init__(self)
				self.script = False
				self.jsreg = re.compile(
					r'var\s+a\s*=\s*(\d+);[\s\S]*'
					r'\.\s*omg\s*=\s*("[^\r\n]*")\s*\.\s*substr\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*;[\s\S]*'
					r'\.href\s*=\s*("[^\r\n]*")\s*\+\s*'
					r'\(\s*Math\s*.\s*pow\s*\(\s*a\s*,\s*(\d+)\s*\)\s*\+\s*b\s*\)\s*\+\s*'
					r'("[^\r\n]*")\s*;',
					re.MULTILINE
				)
				self.jsdata = None

			def handle_starttag(self, tag, attrs):
				self.script = tag.lower() == 'script'

			def handle_data(self, data):
				if not self.script:
					return
				src = data.strip()
				m = self.jsreg.match(src)
				if not m:
					return
				self.jsdata = {
					'a': m.group(1),
					'str': m.group(2),
					'substr_start': m.group(3),
					'substr_length': m.group(4),
					'url_head': m.group(5),
					'power': m.group(6),
					'url_tail': m.group(7)
				}

			def result(self):
				return self.jsdata

		parser = TheHTMLParser()
		parser.feed(html)
		jsdata = parser.result()

		js_a = self.js_decode(jsdata['a'])
		js_str = self.js_decode(jsdata['str'])
		js_substr_start = self.js_decode(jsdata['substr_start'])
		js_substr_length = self.js_decode(jsdata['substr_length'])
		js_url_head = self.js_decode(jsdata['url_head'])
		js_power = self.js_decode(jsdata['power'])
		js_url_tail = self.js_decode(jsdata['url_tail'])

		jb_b = min(len(js_str) - js_substr_start, js_substr_length)
		js_computed = pow(js_a, js_power) + jb_b
		href = '%s%s%s' % (js_url_head, js_computed, js_url_tail)
		name = urlunquote(urlparse(href).path).split('/').pop()

		return {
			'href': href,
			'name': name
		}

	def fetch_storage(self, url):
		self.log('Fetching storage: %s' % (url), True)
		(code, headers, body) = self.request_data(self.options.url[0])
		self.assert_status_code(code, 200)

		html = self.request_data_decode(body, headers)
		parsed = self.parse_storage(html)

		href = parsed['href']
		url = urljoin(url, href)
		self.log('Found URL: %s' % (url), True)

		name = parsed['name']
		self.log('Found name: %s' % (name), True)

		return {
			'url': url,
			'name': name
		}

	def create_out_dir(self):
		opt_dir = self.options.dir
		return opt_dir if opt_dir else ''

	def create_file_name_temp(self, file_name):
		return '.%s.%s' % (__prog__, file_name)

	def create_file_name(self, storage):
		opt_file = self.options.file
		if opt_file:
			return opt_file
		return storage['name'] if storage else None

	def download_verify_size(self, file_path, file_size):
		size = self.stat(file_path).st_size
		if size != file_size:
			raise Exception('Unexected download size: %s expected: %s' % (size, file_size))

	def download_set_mtime(self, file_path, file_mtime):
		os.utime(file_path, (file_mtime, file_mtime))

	def assert_not_exists(self, file_path):
		if self.stat(file_path):
			raise Exception('Already exists: %s' % (file_path))

	def download(self):
		out_dir = self.create_out_dir()
		if out_dir:
			self.log('Output dir: %s' % (out_dir))

		file_name = self.create_file_name(None)
		if not file_name is None:
			self.log('Output file: %s' % (file_name))
			self.assert_not_exists(os.path.join(out_dir, file_name))

		storage = self.fetch_storage(self.options.url[0])
		url = storage['url']

		if file_name is None:
			file_name = self.create_file_name(storage)
			self.log('Output file: %s' % (file_name))

		file_name_path = os.path.join(out_dir, file_name)
		self.assert_not_exists(file_name_path)

		file_name_temp = self.create_file_name_temp(file_name)
		self.log('Temporary file: %s' % (file_name_temp), True)

		file_name_temp_path = os.path.join(out_dir, file_name_temp)

		# Download with progress info, adding new line to clear after.
		try:
			dlinfo = self.request_download(url, file_name_temp_path, self.download_progress, True)
		finally:
			self.log('')

		# Verify size.
		storage_size = dlinfo['size']
		if storage_size is None:
			self.log('Cannot verifying size, unknown', True)
		else:
			self.log('Verifying size: %s' % (storage_size), True)
			try:
				self.download_verify_size(file_name_temp_path, storage_size)
			except Exception as ex:
				os.remove(file_name_temp_path)
				raise ex

		# Set the modified time if requested.
		if self.options.mtime:
			storage_mtime = dlinfo['modified']
			if storage_mtime is None:
				self.log('Cannot set mtime, unknown', True)
			else:
				self.log('Setting mtime: %s' % (storage_mtime), True)
				self.download_set_mtime(file_name_temp_path, storage_mtime)

		os.rename(file_name_temp_path, file_name_path)
		self.log('Done')

	def download_progress(self, status, start, now, offset, added, current, total):
		if status is self.DL_PROGRESS_READ:
			return

		if status is self.DL_PROGRESS_START:
			self.output_progress_start()
			return

		delta = now - start
		sub_total = total - offset
		sub_current = current - offset
		sub_remain = sub_total - sub_current
		bytes_sec = sub_current / float(delta) if delta else 0
		delta_remain = sub_remain / float(bytes_sec) if bytes_sec else 0

		timestr = self.seconds_human(math.floor(delta))
		percent = self.percent_human(current, total)
		amount = '%s (%s) / %s (%s)' % (
			self.bytes_human(current),
			current,
			self.bytes_human(total),
			total
		)
		persec = '%s/s' % (self.bytes_human(round(bytes_sec)))
		timerem = self.seconds_human(math.ceil(delta_remain))

		self.output_progress('  '.join([
			'',
			timestr,
			percent,
			amount,
			persec,
			timerem
		]))

	def run(self):
		self.download()

	def main(self):
		def exception(ex):
			if self.options.debug:
				raise ex
			s = str(ex)
			if not s:
				s = ex.__class__.__name__
			self.output('Error: %s' % (s))
			return 1
		try:
			self.run()
		except Exception as ex:
			return exception(ex)
		except KeyboardInterrupt as ex:
			return exception(ex)
		return 0

def main():
	parser = argparse.ArgumentParser(
		prog=__prog__,
		description=os.linesep.join([
			'%s %s' % (__prog__, __version__),
			'%s %s' % (__copyright__, __license__)
		]),
		formatter_class=argparse.RawTextHelpFormatter
	)
	parser.add_argument(
		'-v',
		'--version',
		action='version',
		version=__version__,
		help='Print version'
	)
	parser.add_argument(
		'-V',
		'--verbose',
		action='store_true',
		help='Verbose mode'
	)
	parser.add_argument(
		'-D',
		'--debug',
		action='store_true',
		help='Debug output'
	)
	parser.add_argument(
		'-B',
		'--buffer',
		type=int,
		default=1024,
		help='Buffer size'
	)
	parser.add_argument(
		'-t',
		'--timeout',
		type=int,
		default=60,
		help='Request timeout in seconds'
	)
	parser.add_argument(
		'-M',
		'--mtime',
		action='store_true',
		help='Use server modified time'
	)
	parser.add_argument(
		'-d',
		'--dir',
		default=None,
		help='Output directory'
	)
	parser.add_argument(
		'url',
		nargs=1,
		help='URL'
	)
	parser.add_argument(
		'file',
		nargs='?',
		default=None,
		help='FILE'
	)
	options = parser.parse_args()
	return Main(options).main()

if __name__ == '__main__':
	sys.exit(main())
