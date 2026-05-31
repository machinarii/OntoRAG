#!/bin/bash

NAMESPACE=rag
helm uninstall ontorag --namespace $NAMESPACE
