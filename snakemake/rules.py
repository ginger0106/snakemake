# -*- coding: utf-8 -*-

import os, re, sys, inspect, sre_constants
from collections import defaultdict

from snakemake.io import IOFile, _IOFile, protected, temp, dynamic, Namedlist, expand, InputFiles, OutputFiles, Wildcards
from snakemake.exceptions import RuleException, IOFileException

__author__ = "Johannes Köster"

class Rule:
	def __init__(self, *args, lineno = None, snakefile = None):
		"""
		Create a rule
		
		Arguments
		name -- the name of the rule
		"""
		if len(args) == 2:
			name, workflow = args
			self.name = name
			self.workflow = workflow
			self.docstring = None
			self.message = None
			self._input = InputFiles()
			self._output = OutputFiles()
			self.dynamic_output = set()
			self.dynamic_input = set()
			self.temp_output = set()
			self.protected_output = set()
			self.threads = 1
			self.priority = 1
			self._log = None
			self.wildcard_names = set()
			self.lineno = lineno
			self.snakefile = snakefile
			self.run_func = None
			self.shellcmd = None
		elif len(args) == 1:
			other = args[0]
			self.name = other.name
			self.workflow = other.workflow
			self.docstring = other.docstring
			self.message = other.message
			self._input = other._input
			self._output = other._output
			self.dynamic_output = other.dynamic_output
			self.dynamic_input = other.dynamic_input
			self.temp_output = other.temp_output
			self.protected_output = other.protected_output
			self.threads = other.threads
			self.priority = other.priority
			self._log = other._log
			self.wildcard_names = other.wildcard_names
			self.lineno = other.lineno
			self.snakefile = other.snakefile
			self.run_func = other.run_func
			self.shellcmd = other.shellcmd
	
	def dynamic_branch(self, wildcards, input=True):
		def get_io(rule):
			return (self.input, self.dynamic_input) if input else (self.output, self.dynamic_output)
		io, dynamic_io = get_io(self)
		expansion = defaultdict(list)
		for i, f in enumerate(io):
			if f in dynamic_io:
				try:
					for e in reversed(expand(f, zip, **wildcards)):
						expansion[i].append(IOFile(e, rule=self))
				except KeyError:
					return None
		branch = Rule(self)
		io_, dynamic_io_ = get_io(branch)
		
		# replace the dynamic files with the expanded files
		for i, e in reversed(list(expansion.items())):
			dynamic_io_.remove(io[i])
			io_.insert_items(i, e)
		if not input:
			branch.wildcard_names.clear()
			non_dynamic_wildcards = dict((name, values[0]) for name, values in wildcards.items() if len(set(values)) == 1)
			branch._input, branch._output, branch._log = branch.expand_wildcards(wildcards=non_dynamic_wildcards)
			return branch, non_dynamic_wildcards
		return branch

	def has_wildcards(self):
		"""
		Return True if rule contains wildcards.
		"""
		return bool(self.wildcard_names)
	
	@property
	def log(self):
		return self._log
	
	@log.setter
	def log(self, log):
		self._log = IOFile(log, rule=self)

	@property
	def input(self):
		return self._input

	def set_input(self, *input, **kwinput):
		"""
		Add a list of input files. Recursive lists are flattened.
		
		Arguments
		input -- the list of input files
		"""
		for item in input:
			self._set_inoutput_item(item)
		for name, item in kwinput.items():
			self._set_inoutput_item(item, name = name)

	@property
	def output(self):
		return self._output

	def set_output(self, *output, **kwoutput):
		"""
		Add a list of output files. Recursive lists are flattened.
		
		Arguments
		output -- the list of output files
		"""
		for item in output:
			self._set_inoutput_item(item, output = True)
		for name, item in kwoutput.items():
			self._set_inoutput_item(item, output = True, name = name)
		
		for item in self.output:
			if self.dynamic_output and item not in self.dynamic_output:
				raise SyntaxError("A rule with dynamic output may not define any non-dynamic output files.")
			wildcards = item.get_wildcard_names()
			if self.wildcard_names:
				if self.wildcard_names != wildcards:
					raise SyntaxError("Not all output files of rule {} contain the same wildcards.".format(self.name))
			else:
				self.wildcard_names = wildcards
	
	def _set_inoutput_item(self, item, output = False, name=None):
		"""
		Set an item to be input or output.
		
		Arguments
		item     -- the item
		inoutput -- either a Namedlist of input or output items
		name     -- an optional name for the item
		"""
		inoutput = self.output if output else self.input
		if isinstance(item, str):
			_item = IOFile(item, rule=self)
			if isinstance(item, temp):
				if not output:
					raise SyntaxError("Only output files may be temporary")
				self.temp_output.add(_item)
			if isinstance(item, protected):
				if not output:
					raise SyntaxError("Only output files may be protected")
				self.protected_output.add(_item)
			if isinstance(item, dynamic):
				if output:
					self.dynamic_output.add(_item)
				else:
					self.dynamic_input.add(_item)
			inoutput.append(_item)
			if name:
				inoutput.add_name(name)
		elif inspect.isfunction(item):
			if output:
				raise SyntaxError("Only input files can be specified as functions")
			inoutput.append(item)
			if name:
				inoutput.add_name(name)
		else:
			try:
				start = len(inoutput)
				for i in item:
					self._set_inoutput_item(i, output = output)
				if name:
					# if the list was named, make it accessible via the Namedlist
					inoutput.set_name(name, start, end=len(inoutput))
			except TypeError:
				raise SyntaxError("Input and output files must be specified as strings.")
		
	def expand_wildcards(self, wildcards = None):
		""" Expand wildcards depending on the requested output or given wildcards dict. """
		if wildcards is None:
			wildcards = dict()
		
#		if self.name == "uniprot2hgnc":
#			import pdb; pdb.set_trace()
		missing_wildcards = self.wildcard_names - set(wildcards.keys()) # TODO validate
		
		if missing_wildcards:
			raise RuleException("Could not resolve wildcards in rule {}:\n{}".format(self.name, "\n".join(self.wildcard_names)), lineno = self.lineno, snakefile = self.snakefile)

		try:
			input = InputFiles()
			wildcards_obj = Wildcards(fromdict=wildcards)
			for name, f in self.input.allitems():
				start = len(input)
				if inspect.isfunction(f):
					files = f(wildcards_obj)
					if isinstance(files, str):
						files = [files]
					for f_ in files:
						input.append(IOFile(f_, rule=self))
				else:
					if isinstance(f, str):
						f = [f]
					for f_ in f:
						input.append(f_.apply_wildcards(wildcards, fill_missing=f_ in self.dynamic_input, fail_dynamic=self.dynamic_output))
				if name:
					input.set_name(name, start, end=len(input))
					
			#input = InputFiles(f.apply_wildcards(wildcards, fill_missing=f in self.dynamic_input, fail_dynamic=self.dynamic_output) for f in self.input)
			output = OutputFiles(o.apply_wildcards(wildcards) for o in self.output)
			#input.take_names(self.input.get_names())
			output.take_names(self.output.get_names())
			log = self.log.apply_wildcards(wildcards) if self.log else None
			return input, output, log
		except KeyError as ex:
			# this can only happen if an input file contains an unresolved wildcard.
			raise RuleException("Wildcards in input or log file of rule {} cannot be determined from output files:\n{}".format(self, str(ex)), lineno = self.lineno, snakefile = self.snakefile)

	def is_producer(self, requested_output):
		"""
		Returns True if this rule is a producer of the requested output.
		"""
		try:
			for o in self.output:
				match = o.match(requested_output)
				if match and len(match.group()) == len(requested_output):
					return True
			return False
		except sre_constants.error as ex:
			raise IOFileException("{} in wildcard statement".format(ex), snakefile=self.snakefile, lineno=self.lineno)
		except ValueError as ex:
			raise IOFileException("{}".format(ex), snakefile=self.snakefile, lineno=self.lineno)

	def get_wildcards(self, requested_output):
		"""
		Update the given wildcard dictionary by matching regular expression output files to the requested concrete ones.
		
		Arguments
		wildcards -- a dictionary of wildcards
		requested_output -- a concrete filepath
		"""
		if requested_output is None:
			return dict()
		bestmatchlen = 0
		bestmatch = None
		bestmatch_output = None
		for i, o in enumerate(self.output):
			match = o.match(requested_output)
			if match:
				l = self.get_wildcard_len(match.groupdict())
				if not bestmatch or bestmatchlen > l:
					bestmatch = match.groupdict()
					bestmatchlen = l
					bestmatch_output = self.output[i]
		return bestmatch
	
	@staticmethod
	def get_wildcard_len(wildcards):
		"""
		Return the length of the given wildcard values.
		
		Arguments
		wildcards -- a dict of wildcards
		"""
		return sum(map(len, wildcards.values()))

	def __lt__(self, rule):
		comp = self.workflow._ruleorder.compare(self.name, rule.name)
		return comp < 0

	def __gt__(self, rule):
		comp = self.workflow._ruleorder.compare(self.name, rule.name)
		return comp > 0

	def __str__(self):
		return self.name

class Ruleorder:
	def __init__(self):
		self.order = list()

	def add(self, *rulenames):
		"""
		Records the order of given rules as rule1 > rule2 > rule3, ...
		"""
		self.order.append(list(rulenames))

	def compare(self, rule1name, rule2name):
		"""
		Return whether rule2 has a higher priority that rule1.
		"""
		# try the last clause first, i.e. clauses added later overwrite those before.
		for clause in reversed(self.order):
			try:
				i = clause.index(rule1name)
				j = clause.index(rule2name)
				# rules with higher priority should have a smaller index
				comp = j - i
				if comp < 0: 
					comp = -1
				elif comp > 0:
					comp = 1
				return comp
			except ValueError:
				pass
		return 0