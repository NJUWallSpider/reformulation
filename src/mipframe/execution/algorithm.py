"""Array algorithm context and independent typed result leases."""
from mipframe.frame.executor import Context
from mipframe.frame.array_source import frame_from_columns
from mipframe.data.runtime import WorkContext
from mipframe.core.errors import QueryTooLarge


class ArrayAlgorithmStore(Context):
    def __init__(self,models=(),*,memory_mb=128,max_output_rows=1_000_000,disk_mb=None,work=None):
        if max_output_rows is not None and (type(max_output_rows) is not int or max_output_rows<0):
            raise ValueError('max_output_rows must be a nonnegative integer or None')
        super().__init__(models,memory_mb,disk_mb,work=work)
        self.limit=max_output_rows
        self.paths={'typed arrays'}
        self.operation=''

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def enforce(self,n):
        if self.limit is not None and n>self.limit:raise QueryTooLarge(self.limit,n)

    def disk_check(self):
        self.guard()

    def invalid(self,message,schema,rows,*,relations=None):
        from mipframe.check.report import CardinalityError,Report
        violations=self.publish(schema,rows,relations=relations,limited=False)
        raise CardinalityError(message,Report(self,False,
            dict(operation=self.operation,violations=len(violations)),violations))

    def publish(self,schema,records,*,key=None,domains=None,relations=None,limited=True):
        from mipframe.frame.schema import frozen_schema
        from mipframe.frame.materialized import stored_frame
        from mipframe.data.relations import build
        output=Context(self.models,work=self.work)
        def rows():
            for i,row in enumerate(records,1):
                if limited:self.enforce(i)
                yield row
        try:
            table=build(output.work,frozen_schema(schema),rows())
            return stored_frame(output,table,schema,key=key,domains=domains,relations=relations)
        except BaseException:output.close();raise

    def publish_batches(self,schema,batches,*,key=None,domains=None,relations=None,limited=True):
        from mipframe.frame.schema import frozen_schema
        from mipframe.frame.materialized import stored_frame
        from mipframe.data.relations import RelationBuilder
        output=Context(self.models,work=self.work)
        try:
            builder=RelationBuilder(output.work,frozen_schema(schema))
            for batch in batches:
                if limited:self.enforce(builder.count+batch.size)
                builder.extend(batch)
            return stored_frame(output,builder.finish(),schema,key=key,domains=domains,relations=relations)
        except BaseException:output.close();raise

    def close(self):
        if hasattr(self,'work') and not self.work.closed:self.work.close()
        super().close()


class ArrayArtifact:
    """Completed result lease, independent of the algorithm's scratch lifetime."""
    def __init__(self,memory_mb=128,disk_bytes=None,*,work=None):
        self.work=work.fork() if work is not None else WorkContext(memory_mb,None if disk_bytes is None else disk_bytes/1024**2)
    def guard(self):
        if self.work.closed:
            from mipframe.store.numbers import MIPFrameError
            raise MIPFrameError('Array result is closed')
    def close(self):self.work.close()
    def __del__(self):self.close()
